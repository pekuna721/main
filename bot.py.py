import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict
import discord
from discord.ext import commands
from discord import app_commands

TOKEN = os.environ["DISCORD_TOKEN"]

# ========== ヘルスチェック用サーバー ==========
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8000), HealthCheckHandler)
    server.serve_forever()

# Bot起動前にスレッドで起動
threading.Thread(target=run_health_server, daemon=True).start()
# =============================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

class AntiSpamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild_settings = defaultdict(lambda: {
            "enabled": False,
            "max_msg": 5,
            "time_window": 8,
            "action": "delete",
            "ignored_channels": [],
            "ignored_roles": [],
        })
        self.user_messages = defaultdict(lambda: defaultdict(list))

    antispam_group = app_commands.Group(name="antispam", description="アンチスパム設定")

    @antispam_group.command(name="panel", description="管理パネルを表示")
    async def panel(self, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or await self.bot.is_owner(interaction.user)):
            await interaction.response.send_message("管理者のみ実行できます。", ephemeral=True)
            return
        guild_id = interaction.guild_id
        settings = self.guild_settings[guild_id]
        embed = self._build_embed(settings)
        view = AntiSpamView(self, guild_id, settings)
        await interaction.response.send_message(embed=embed, view=view)

    def _build_embed(self, settings: dict) -> discord.Embed:
        action_emoji = {
            "warn": "⚠️ 警告のみ",
            "delete": "🗑️ 削除",
            "timeout": "🔇 タイムアウト(5分)",
            "ban": "🔨 BAN"
        }
        status = "🟢 ON" if settings["enabled"] else "🔴 OFF"
        embed = discord.Embed(
            title="アンチスパム管理パネル",
            description=(
                f"状態: {status}\n"
                f"制限: **{settings['max_msg']}回 / {settings['time_window']}秒**\n"
                f"違反時アクション: **{action_emoji.get(settings['action'], settings['action'])}**\n"
                f"除外ロール: {len(settings['ignored_roles'])}個\n"
                f"除外チャンネル: {len(settings['ignored_channels'])}個"
            ),
            color=discord.Color.blue()
        )
        return embed

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id
        settings = self.guild_settings[guild_id]

        if not settings["enabled"]:
            return

        if message.channel.id in settings["ignored_channels"]:
            return

        if any(role.id in settings["ignored_roles"] for role in message.author.roles):
            return

        user_id = message.author.id
        now = time.time()
        timestamps = self.user_messages[guild_id][user_id]

        max_msg = settings["max_msg"]
        time_window = settings["time_window"]
        action = settings["action"]

        timestamps[:] = [t for t in timestamps if now - t < time_window]
        timestamps.append(now)

        if len(timestamps) > max_msg:
            if action == "warn":
                try:
                    await message.author.send(
                        f"⚠️ {message.guild.name} で連投が検出されました。\n"
                        f"{time_window}秒以内に{max_msg}回以上の送信は控えてください。"
                    )
                except:
                    pass

            elif action == "delete":
                try:
                    await message.delete()
                    await message.author.send(
                        f"🗑️ {message.guild.name} で連投メッセージを削除しました。\n"
                        f"制限: {time_window}秒/{max_msg}回"
                    )
                except:
                    pass

            elif action == "timeout":
                try:
                    await message.delete()
                    await message.author.timeout(
                        discord.utils.utcnow() + discord.timedelta(minutes=5),
                        reason=f"連投スパム ({time_window}秒/{max_msg}回超過)"
                    )
                    await message.author.send(
                        f"🔇 {message.guild.name} で5分間タイムアウトされました。"
                    )
                except:
                    pass

            elif action == "ban":
                try:
                    await message.delete()
                    await message.author.ban(
                        reason=f"連投スパム ({time_window}秒/{max_msg}回超過)",
                        delete_message_days=1
                    )
                    await message.author.send(
                        f"🔨 {message.guild.name} からBANされました。"
                    )
                except:
                    pass

    def update_setting(self, guild_id, key, value):
        if key in ("max_msg", "time_window"):
            self.guild_settings[guild_id][key] = int(value)
        elif key == "action":
            self.guild_settings[guild_id][key] = value
        elif key == "add_ignored_role":
            if value not in self.guild_settings[guild_id]["ignored_roles"]:
                self.guild_settings[guild_id]["ignored_roles"].append(value)
        elif key == "remove_ignored_role":
            if value in self.guild_settings[guild_id]["ignored_roles"]:
                self.guild_settings[guild_id]["ignored_roles"].remove(value)
        elif key == "add_ignored_channel":
            if value not in self.guild_settings[guild_id]["ignored_channels"]:
                self.guild_settings[guild_id]["ignored_channels"].append(value)
        elif key == "remove_ignored_channel":
            if value in self.guild_settings[guild_id]["ignored_channels"]:
                self.guild_settings[guild_id]["ignored_channels"].remove(value)


class ActionSelect(discord.ui.Select):
    def __init__(self, cog, guild_id, parent_view):
        self.cog = cog
        self.guild_id = guild_id
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label="⚠️ 警告のみ", value="warn", description="DMで警告、メッセージは残す"),
            discord.SelectOption(label="🗑️ 削除", value="delete", description="メッセージ削除 + DM警告"),
            discord.SelectOption(label="🔇 タイムアウト5分", value="timeout", description="削除 + 5分タイムアウト"),
            discord.SelectOption(label="🔨 BAN", value="ban", description="即BAN（注意！）"),
        ]
        super().__init__(placeholder="違反時のアクションを選択", options=options, row=4)

    async def callback(self, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or await self.cog.bot.is_owner(interaction.user)):
            await interaction.response.send_message("管理者のみ変更できます。", ephemeral=True)
            return
        self.cog.update_setting(self.guild_id, "action", self.values[0])
        settings = self.cog.guild_settings[self.guild_id]
        embed = self.cog._build_embed(settings)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class SettingModal(discord.ui.Modal):
    def __init__(self, cog, guild_id, key, current, embed_builder, parent_view):
        title_map = {"max_msg": "最大メッセージ数", "time_window": "検出時間(秒)"}
        super().__init__(title=f"{title_map[key]}変更")
        self.cog = cog
        self.guild_id = guild_id
        self.key = key
        self.embed_builder = embed_builder
        self.parent_view = parent_view
        self.input_field = discord.ui.TextInput(
            label=f"現在の値: {current}",
            default=str(current),
            required=True
        )
        self.add_item(self.input_field)

    async def on_submit(self, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or await self.cog.bot.is_owner(interaction.user)):
            await interaction.response.send_message("管理者のみ変更できます。", ephemeral=True)
            return
        try:
            val = int(self.input_field.value)
        except ValueError:
            await interaction.response.send_message("数値を入力してください。", ephemeral=True)
            return
        if val < 1:
            await interaction.response.send_message("1以上の値を指定してください。", ephemeral=True)
            return
        self.cog.update_setting(self.guild_id, self.key, val)
        settings = self.cog.guild_settings[self.guild_id]
        embed = self.embed_builder(settings)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class AntiSpamView(discord.ui.View):
    def __init__(self, cog, guild_id, settings):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.add_item(ActionSelect(cog, guild_id, self))
        self.update_buttons(settings)

    def update_buttons(self, settings: dict):
        self.toggle_btn.label = "全体OFF" if settings["enabled"] else "全体ON"
        self.toggle_btn.style = discord.ButtonStyle.danger if settings["enabled"] else discord.ButtonStyle.success
        self.msg_btn.label = f"回数 ({settings['max_msg']})"
        self.time_btn.label = f"秒数 ({settings['time_window']}秒)"

    @discord.ui.button(label="全体ON", style=discord.ButtonStyle.success, row=1)
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or await self.cog.bot.is_owner(interaction.user)):
            await interaction.response.send_message("管理者のみ操作できます。", ephemeral=True)
            return
        settings = self.cog.guild_settings[self.guild_id]
        settings["enabled"] = not settings["enabled"]
        embed = self.cog._build_embed(settings)
        self.update_buttons(settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="回数", style=discord.ButtonStyle.primary, row=2)
    async def msg_btn(self, interaction, button):
        if not (interaction.user.guild_permissions.administrator or await self.cog.bot.is_owner(interaction.user)):
            await interaction.response.send_message("管理者のみ操作できます。", ephemeral=True)
            return
        settings = self.cog.guild_settings[self.guild_id]
        modal = SettingModal(self.cog, self.guild_id, "max_msg", settings["max_msg"], self.cog._build_embed, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="秒数", style=discord.ButtonStyle.primary, row=2)
    async def time_btn(self, interaction, button):
        if not (interaction.user.guild_permissions.administrator or await self.cog.bot.is_owner(interaction.user)):
            await interaction.response.send_message("管理者のみ操作できます。", ephemeral=True)
            return
        settings = self.cog.guild_settings[self.guild_id]
        modal = SettingModal(self.cog, self.guild_id, "time_window", settings["time_window"], self.cog._build_embed, self)
        await interaction.response.send_modal(modal)


@bot.event
async def on_ready():
    print(f"ログイン: {bot.user}")
    await bot.add_cog(AntiSpamCog(bot))
    await bot.tree.sync()
    print("同期完了")

@bot.hybrid_command(name="ping", description="Pingを返します")
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")

if __name__ == "__main__":
    bot.run(TOKEN)
