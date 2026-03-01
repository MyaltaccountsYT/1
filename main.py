import discord
from discord.ext import commands
import configparser
import asyncio
import json
import pathlib
from datetime import datetime, timezone

config = configparser.ConfigParser()
config.read("config.ini")

MAIN_ADMIN_ID = int(config["settings"]["main_admin_id"])
TOKEN = config["settings"]["token"]

ORANGE = 0xFF6600
GREEN  = 0x2ECC71
RED    = 0xE74C3C
FOOTER = "Vortex Core | VPS Core"

DB_PATH   = pathlib.Path("/database/vps_data.json")
META_PATH = pathlib.Path("/database/meta.json")

OS_OPTIONS = [
    discord.SelectOption(label="Ubuntu 20.04",    value="ubuntu:20.04"),
    discord.SelectOption(label="Ubuntu 22.04",    value="ubuntu:22.04"),
    discord.SelectOption(label="Ubuntu 24.04",    value="ubuntu:24.04"),
    discord.SelectOption(label="Debian 11",       value="debian:11"),
    discord.SelectOption(label="Debian 12",       value="debian:12"),
    discord.SelectOption(label="Debian 13",       value="debian:13"),
    discord.SelectOption(label="Fedora 39",       value="fedora:39"),
    discord.SelectOption(label="Fedora 40",       value="fedora:40"),
    discord.SelectOption(label="CentOS Stream 9", value="centos:9-Stream"),
    discord.SelectOption(label="AlmaLinux 9",     value="almalinux:9"),
    discord.SelectOption(label="Rocky Linux 9",   value="rockylinux:9"),
    discord.SelectOption(label="Alpine 3.19",     value="alpine:3.19"),
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=".", intents=intents)

pending_deployments: dict = {}


def _ensure_dir(path: pathlib.Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def db_load() -> dict:
    if not DB_PATH.exists():
        _ensure_dir(DB_PATH)
        DB_PATH.write_text(json.dumps({}))
        return {}
    try:
        return json.loads(DB_PATH.read_text())
    except Exception:
        return {}


def db_save(data: dict):
    _ensure_dir(DB_PATH)
    DB_PATH.write_text(json.dumps(data, indent=2))


def db_get_vps(user_id: int):
    return db_load().get(str(user_id))


def db_set_vps(user_id: int, vps: dict):
    data = db_load()
    data[str(user_id)] = vps
    db_save(data)


def db_delete_vps(user_id: int):
    data = db_load()
    data.pop(str(user_id), None)
    db_save(data)


def meta_load() -> dict:
    if not META_PATH.exists():
        _ensure_dir(META_PATH)
        META_PATH.write_text(json.dumps({"maintenance": False, "admins": []}))
        return {"maintenance": False, "admins": []}
    try:
        return json.loads(META_PATH.read_text())
    except Exception:
        return {"maintenance": False, "admins": []}


def meta_save(data: dict):
    _ensure_dir(META_PATH)
    META_PATH.write_text(json.dumps(data, indent=2))


def is_maintenance() -> bool:
    return meta_load().get("maintenance", False)


def get_admins() -> list[int]:
    return [int(x) for x in meta_load().get("admins", [])]


def is_admin(user_id: int) -> bool:
    return user_id == MAIN_ADMIN_ID or user_id in get_admins()


def make_embed(title, description=None, fields=None, color=ORANGE):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=FOOTER)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    return embed


async def lxc_exec(ct_name: str, cmd: str):
    proc = await asyncio.create_subprocess_exec(
        "sudo", "lxc-attach", "-n", ct_name, "--", "bash", "-c", cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(), err.decode()


async def get_container_status(ct_name: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "sudo", "lxc-info", "-n", ct_name, "-s",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    text = out.decode().lower()
    if "running" in text:
        return "online"
    if "stopped" in text:
        return "offline"
    return "unknown"


async def get_cpu_usage(ct_name: str) -> str:
    _, out, _ = await lxc_exec(
        ct_name,
        "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1 2>/dev/null || echo 'N/A'",
    )
    val = out.strip()
    return f"{val}%" if val and val != "N/A" else "N/A"


async def get_memory_usage(ct_name: str) -> str:
    _, out, _ = await lxc_exec(
        ct_name,
        "free -m | awk '/Mem:/{printf \"%dMB / %dMB\", $3, $2}' 2>/dev/null || echo 'N/A'",
    )
    return out.strip() or "N/A"


async def get_disk_usage(ct_name: str) -> str:
    _, out, _ = await lxc_exec(
        ct_name,
        "df -h / | awk 'NR==2{print $3\" / \"$2}' 2>/dev/null || echo 'N/A'",
    )
    return out.strip() or "N/A"


async def build_manage_embed(vps: dict, display_user: discord.User, admin_view: bool = False):
    ct_name = vps["ct_name"]
    status   = await get_container_status(ct_name)
    status_icon  = "🟢 Online" if status == "online" else "🔴 Offline"
    embed_color  = GREEN if status == "online" else RED

    cpu_usage = mem_usage = disk_usage = "N/A"
    if status == "online":
        cpu_usage  = await get_cpu_usage(ct_name)
        mem_usage  = await get_memory_usage(ct_name)
        disk_usage = await get_disk_usage(ct_name)

    title = f"VPS Management — {display_user.display_name}"
    if admin_view:
        title = f"[Admin] VPS — {display_user.display_name}"

    embed = discord.Embed(title=title, description=f"**Status:** {status_icon}", color=embed_color)
    embed.set_footer(text=FOOTER)
    embed.add_field(name="Container",     value=f"`{ct_name}`",                inline=True)
    embed.add_field(name="OS",            value=vps.get("os", "Unknown"),       inline=True)
    embed.add_field(name="Created",       value=vps.get("created_at", "?"),     inline=True)
    embed.add_field(name="RAM",           value=f"{vps.get('ram','?')} GB",     inline=True)
    embed.add_field(name="CPU",           value=f"{vps.get('cpu','?')} Core(s)",inline=True)
    embed.add_field(name="Disk",          value=f"{vps.get('disk','?')} GB",    inline=True)
    embed.add_field(name="CPU Usage",     value=cpu_usage,                      inline=True)
    embed.add_field(name="Memory Usage",  value=mem_usage,                      inline=True)
    embed.add_field(name="Disk Usage",    value=disk_usage,                     inline=True)
    embed.add_field(name="Plan Cost",     value=f"${vps.get('total','?')}/mo",  inline=True)
    return embed, status


async def tmate_link_for(ct_name: str) -> str | None:
    rc, out, _ = await lxc_exec(
        ct_name,
        "tmate -S /tmp/tmate.sock new-session -d 2>/dev/null; sleep 1; "
        "tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}' 2>/dev/null",
    )
    link = out.strip()
    if not link or rc != 0:
        _, out2, _ = await lxc_exec(ct_name, "tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}' 2>/dev/null")
        link = out2.strip()
    return link or None


class ManageView(discord.ui.View):
    """Panel shown to the VPS owner."""

    def __init__(self, user_id: int, vps: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.vps     = vps

    def _ok(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This is not your VPS panel."), ephemeral=True)
            return
        await interaction.response.defer()
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) == "online":
            await interaction.followup.send(embed=make_embed("Already Running", "Your VPS is already online."), ephemeral=True)
            return
        proc = await asyncio.create_subprocess_exec("sudo", "lxc-start", "-n", ct_name,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            await interaction.followup.send(embed=make_embed("Start Failed", f"```{err.decode()[:800]}```"), ephemeral=True)
            return
        await asyncio.sleep(2)
        owner = await bot.fetch_user(self.user_id)
        embed, _ = await build_manage_embed(self.vps, owner)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(embed=make_embed("VPS Started ✅", "Your container is now online."), ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This is not your VPS panel."), ephemeral=True)
            return
        await interaction.response.defer()
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) == "offline":
            await interaction.followup.send(embed=make_embed("Already Stopped", "Your VPS is already offline."), ephemeral=True)
            return
        proc = await asyncio.create_subprocess_exec("sudo", "lxc-stop", "-n", ct_name,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        owner = await bot.fetch_user(self.user_id)
        embed, _ = await build_manage_embed(self.vps, owner)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(embed=make_embed("VPS Stopped ⏹️", "Your container has been stopped."), ephemeral=True)

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This is not your VPS panel."), ephemeral=True)
            return
        await interaction.response.defer()
        ct_name = self.vps["ct_name"]
        for cmd in [["sudo", "lxc-stop", "-n", ct_name], ["sudo", "lxc-start", "-n", ct_name]]:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
            await asyncio.sleep(2)
        owner = await bot.fetch_user(self.user_id)
        embed, _ = await build_manage_embed(self.vps, owner)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(embed=make_embed("VPS Restarted 🔄", "Your container has been restarted."), ephemeral=True)

    @discord.ui.button(label="SSH Access", style=discord.ButtonStyle.secondary, emoji="🔐", row=0)
    async def ssh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This is not your VPS panel."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) != "online":
            await interaction.followup.send(embed=make_embed("VPS Offline", "Start your VPS before requesting SSH access."), ephemeral=True)
            return
        link = await tmate_link_for(ct_name)
        if not link:
            await interaction.followup.send(embed=make_embed("SSH Failed", "Could not generate a tmate session."), ephemeral=True)
            return
        try:
            owner = await bot.fetch_user(self.user_id)
            await owner.send(embed=make_embed(
                "🔐 SSH Access — Vortex Core",
                "Your tmate session is ready. **Do not share this link.**",
                fields=[
                    ("SSH Command", f"```{link}```", False),
                    ("Container",   f"`{ct_name}`",  True),
                    ("Expires",     "When the tmate session is closed", True),
                ],
            ))
            await interaction.followup.send(embed=make_embed("SSH Link Sent 🔐", "Your tmate link has been sent to your DMs."), ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=make_embed("DMs Disabled", f"Enable DMs from server members.\n\n```{link}```"),
                ephemeral=True,
            )

    @discord.ui.button(label="Reinstall OS", style=discord.ButtonStyle.danger, emoji="💿", row=1)
    async def reinstall_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This is not your VPS panel."), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=make_embed("⚠️ Reinstall OS", "This will **destroy all data**. Select a new OS and confirm."),
            view=ReinstallConfirmView(self.user_id, self.vps),
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh Stats", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This is not your VPS panel."), ephemeral=True)
            return
        await interaction.response.defer()
        owner = await bot.fetch_user(self.user_id)
        embed, _ = await build_manage_embed(self.vps, owner)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(embed=make_embed("Stats Refreshed 📊", "Live stats have been updated."), ephemeral=True)

    @discord.ui.button(label="Console Log", style=discord.ButtonStyle.secondary, emoji="🖥️", row=1)
    async def console_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This is not your VPS panel."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) != "online":
            await interaction.followup.send(embed=make_embed("VPS Offline", "Start your VPS to view console output."), ephemeral=True)
            return
        _, out, _ = await lxc_exec(ct_name, "journalctl -n 30 --no-pager 2>/dev/null || dmesg | tail -30 2>/dev/null || echo 'No logs available'")
        log_text = out.strip()[:1800] or "No output available."
        await interaction.followup.send(embed=make_embed("🖥️ Console Output (last 30 lines)", f"```{log_text}```"), ephemeral=True)

    @discord.ui.button(label="Network Info", style=discord.ButtonStyle.secondary, emoji="🌐", row=1)
    async def network_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This is not your VPS panel."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) != "online":
            await interaction.followup.send(embed=make_embed("VPS Offline", "Start your VPS to view network info."), ephemeral=True)
            return
        _, out, _  = await lxc_exec(ct_name, "ip addr show 2>/dev/null | grep -E 'inet |inet6 ' | awk '{print $2}' | head -10")
        _, dns, _  = await lxc_exec(ct_name, "cat /etc/resolv.conf 2>/dev/null | grep nameserver | head -5")
        await interaction.followup.send(embed=make_embed(
            "🌐 Network Info", None,
            fields=[
                ("IP Addresses", f"```{out.strip() or 'None'}```", False),
                ("DNS Servers",  f"```{dns.strip() or 'None'}```", False),
            ],
        ), ephemeral=True)


class AdminManageView(discord.ui.View):
    """Restricted panel for admins managing another user's VPS.
    Admins get: Start, Stop, SSH Access (link goes to the VPS owner's DMs),
    Refresh Stats, Console Log, Network Info.
    Reinstall is intentionally excluded — only the main admin path (deletevps/deploy) handles destruction.
    """

    def __init__(self, admin_id: int, owner_id: int, vps: dict):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.owner_id = owner_id
        self.vps      = vps

    def _ok(self, interaction: discord.Interaction) -> bool:
        return is_admin(interaction.user.id) and interaction.user.id == self.admin_id

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This panel is not yours."), ephemeral=True)
            return
        await interaction.response.defer()
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) == "online":
            await interaction.followup.send(embed=make_embed("Already Running", "Container is already online."), ephemeral=True)
            return
        proc = await asyncio.create_subprocess_exec("sudo", "lxc-start", "-n", ct_name,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            await interaction.followup.send(embed=make_embed("Start Failed", f"```{err.decode()[:800]}```"), ephemeral=True)
            return
        await asyncio.sleep(2)
        owner = await bot.fetch_user(self.owner_id)
        embed, _ = await build_manage_embed(self.vps, owner, admin_view=True)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(embed=make_embed("VPS Started ✅", f"Container `{ct_name}` is now online."), ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This panel is not yours."), ephemeral=True)
            return
        await interaction.response.defer()
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) == "offline":
            await interaction.followup.send(embed=make_embed("Already Stopped", "Container is already offline."), ephemeral=True)
            return
        proc = await asyncio.create_subprocess_exec("sudo", "lxc-stop", "-n", ct_name,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        owner = await bot.fetch_user(self.owner_id)
        embed, _ = await build_manage_embed(self.vps, owner, admin_view=True)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(embed=make_embed("VPS Stopped ⏹️", f"Container `{ct_name}` has been stopped."), ephemeral=True)

    @discord.ui.button(label="SSH Access", style=discord.ButtonStyle.secondary, emoji="🔐", row=0)
    async def ssh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This panel is not yours."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) != "online":
            await interaction.followup.send(embed=make_embed("VPS Offline", "Start the VPS first."), ephemeral=True)
            return
        link = await tmate_link_for(ct_name)
        if not link:
            await interaction.followup.send(embed=make_embed("SSH Failed", "Could not generate a tmate session."), ephemeral=True)
            return
        try:
            owner = await bot.fetch_user(self.owner_id)
            await owner.send(embed=make_embed(
                "🔐 SSH Access — Vortex Core",
                f"An admin has opened an SSH session on your VPS. **Do not share this link.**",
                fields=[
                    ("SSH Command", f"```{link}```", False),
                    ("Container",   f"`{ct_name}`",  True),
                    ("Expires",     "When the tmate session is closed", True),
                ],
            ))
            await interaction.followup.send(embed=make_embed("SSH Link Sent 🔐", f"Tmate link sent to the VPS owner's DMs."), ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=make_embed("Owner DMs Disabled", f"Could not DM the owner. Link:\n```{link}```"),
                ephemeral=True,
            )

    @discord.ui.button(label="Refresh Stats", style=discord.ButtonStyle.secondary, emoji="📊", row=0)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This panel is not yours."), ephemeral=True)
            return
        await interaction.response.defer()
        owner = await bot.fetch_user(self.owner_id)
        embed, _ = await build_manage_embed(self.vps, owner, admin_view=True)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send(embed=make_embed("Stats Refreshed 📊", "Live stats updated."), ephemeral=True)

    @discord.ui.button(label="Console Log", style=discord.ButtonStyle.secondary, emoji="🖥️", row=1)
    async def console_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This panel is not yours."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) != "online":
            await interaction.followup.send(embed=make_embed("VPS Offline", "Start the VPS to view logs."), ephemeral=True)
            return
        _, out, _ = await lxc_exec(ct_name, "journalctl -n 30 --no-pager 2>/dev/null || dmesg | tail -30 2>/dev/null || echo 'No logs'")
        await interaction.followup.send(embed=make_embed("🖥️ Console (last 30 lines)", f"```{out.strip()[:1800] or 'Empty'}```"), ephemeral=True)

    @discord.ui.button(label="Network Info", style=discord.ButtonStyle.secondary, emoji="🌐", row=1)
    async def network_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._ok(interaction):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This panel is not yours."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ct_name = self.vps["ct_name"]
        if await get_container_status(ct_name) != "online":
            await interaction.followup.send(embed=make_embed("VPS Offline", "Start the VPS to view network info."), ephemeral=True)
            return
        _, out, _ = await lxc_exec(ct_name, "ip addr show 2>/dev/null | grep -E 'inet |inet6 ' | awk '{print $2}' | head -10")
        _, dns, _ = await lxc_exec(ct_name, "cat /etc/resolv.conf 2>/dev/null | grep nameserver | head -5")
        await interaction.followup.send(embed=make_embed(
            "🌐 Network Info", None,
            fields=[
                ("IP Addresses", f"```{out.strip() or 'None'}```", False),
                ("DNS Servers",  f"```{dns.strip() or 'None'}```", False),
            ],
        ), ephemeral=True)


class ReinstallOSSelect(discord.ui.Select):
    def __init__(self, user_id: int, vps: dict):
        super().__init__(placeholder="Choose new OS...", options=OS_OPTIONS, min_values=1, max_values=1)
        self.user_id = user_id
        self.vps     = vps

    async def callback(self, interaction: discord.Interaction):
        new_os = self.values[0]
        view = ReinstallFinalConfirmView(self.user_id, self.vps, new_os)
        embed = make_embed(
            "Final Confirmation",
            f"Reinstalling **{new_os}** on `{self.vps['ct_name']}`.\n\n⚠️ **ALL DATA WILL BE LOST.** This cannot be undone.",
        )
        await interaction.response.edit_message(embed=embed, view=view)


class ReinstallConfirmView(discord.ui.View):
    def __init__(self, user_id: int, vps: dict):
        super().__init__(timeout=60)
        self.add_item(ReinstallOSSelect(user_id, vps))


class ReinstallFinalConfirmView(discord.ui.View):
    def __init__(self, user_id: int, vps: dict, new_os: str):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.vps     = vps
        self.new_os  = new_os

    @discord.ui.button(label="Confirm Reinstall", style=discord.ButtonStyle.danger, emoji="💿")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id and interaction.user.id != MAIN_ADMIN_ID:
            await interaction.response.send_message(embed=make_embed("Unauthorized", "Not your VPS."), ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        msg = await interaction.followup.send(
            embed=make_embed("Reinstalling OS...", f"Rebuilding `{self.vps['ct_name']}` with `{self.new_os}`. Please wait..."),
            ephemeral=False,
        )
        asyncio.create_task(run_reinstall(msg, self.vps, self.new_os, self.user_id))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=make_embed("Reinstall Cancelled", "No changes were made."), view=None)


async def run_reinstall(msg, vps: dict, new_os: str, user_id: int):
    ct_name   = vps["ct_name"]
    os_name, os_version = new_os.split(":")

    async def upd(title, desc):
        await msg.edit(embed=make_embed(title, desc))

    await upd("Reinstall — Step 1/5", f"Stopping `{ct_name}`...")
    p = await asyncio.create_subprocess_exec("sudo", "lxc-stop", "-n", ct_name, "-k",
                                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await p.communicate()
    await asyncio.sleep(2)

    await upd("Reinstall — Step 2/5", "Destroying container...")
    p = await asyncio.create_subprocess_exec("sudo", "lxc-destroy", "-n", ct_name,
                                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await p.communicate()

    await upd("Reinstall — Step 3/5", f"Creating new container with `{new_os}`...")
    p = await asyncio.create_subprocess_exec(
        "sudo", "lxc-create", "-n", ct_name, "-t", "download",
        "--", "--dist", os_name, "--release", os_version, "--arch", "amd64",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await p.communicate()
    if p.returncode != 0:
        await upd("Reinstall Failed", f"```{err.decode()[:800]}```")
        return

    try:
        with open(f"/var/lib/lxc/{ct_name}/config", "a") as f:
            f.write(f"\nlxc.cgroup2.memory.max = {vps['ram'] * 1024}M\n")
            f.write(f"lxc.cgroup2.cpu.shares = {vps['cpu'] * 1024}\n")
    except Exception:
        pass

    await upd("Reinstall — Step 4/5", "Starting container and configuring DNS...")
    p = await asyncio.create_subprocess_exec("sudo", "lxc-start", "-n", ct_name,
                                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await p.communicate()
    await asyncio.sleep(3)
    await lxc_exec(ct_name, "echo 'nameserver 1.1.1.1\nnameserver 8.8.8.8' > /etc/resolv.conf")

    await upd("Reinstall — Step 5/5", "Running updates and installing packages...")
    await lxc_exec(ct_name, "export DEBIAN_FRONTEND=noninteractive && apt-get update -y && apt-get upgrade -y")
    await lxc_exec(ct_name, "export DEBIAN_FRONTEND=noninteractive && apt-get install -y tmate openssh-server curl python3 python3-pip python3-tk wget lsof")

    data = db_load()
    if str(user_id) in data:
        data[str(user_id)]["os"] = new_os
        db_save(data)

    await msg.edit(embed=make_embed(
        "OS Reinstalled ✅", f"Container `{ct_name}` reinstalled with `{new_os}`.",
        fields=[("OS", new_os, True), ("RAM", f"{vps['ram']} GB", True), ("CPU", f"{vps['cpu']} Core(s)", True)],
    ))
    try:
        owner = await bot.fetch_user(user_id)
        await owner.send(embed=make_embed("VPS Reinstalled 💿", f"Your VPS has been reinstalled with `{new_os}`. All previous data has been wiped."))
    except discord.Forbidden:
        pass


class ResourceModal(discord.ui.Modal, title="Configure VPS Resources"):
    ram  = discord.ui.TextInput(label="RAM (GB) — $1/GB",        placeholder="e.g. 2",  min_length=1, max_length=3)
    cpu  = discord.ui.TextInput(label="CPU Cores — $1/core",     placeholder="e.g. 2",  min_length=1, max_length=2)
    disk = discord.ui.TextInput(label="Disk Space (GB) — $1/GB", placeholder="e.g. 20", min_length=1, max_length=4)

    def __init__(self, target_user: discord.Member):
        super().__init__()
        self.target_user = target_user

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ram_gb     = int(self.ram.value)
            cpu_cores  = int(self.cpu.value)
            disk_gb    = int(self.disk.value)
        except ValueError:
            await interaction.response.send_message(embed=make_embed("Invalid Input", "RAM, CPU, and Disk must be whole numbers."), ephemeral=True)
            return
        total = ram_gb + cpu_cores + disk_gb
        pending_deployments[interaction.user.id] = {
            "target_user": self.target_user,
            "ram": ram_gb, "cpu": cpu_cores, "disk": disk_gb, "total": total,
        }
        embed = make_embed(
            "Select Operating System",
            f"Deploying VPS for {self.target_user.mention}\n\n"
            f"**RAM:** {ram_gb} GB  **CPU:** {cpu_cores} Core(s)  **Disk:** {disk_gb} GB\n"
            f"**Total Cost:** ${total}/mo\n\nChoose an OS below:",
        )
        await interaction.response.send_message(embed=embed, view=OSSelectView(interaction.user.id), ephemeral=True)


class OSSelect(discord.ui.Select):
    def __init__(self, admin_id: int):
        super().__init__(placeholder="Select an operating system...", options=OS_OPTIONS, min_values=1, max_values=1)
        self.admin_id = admin_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(embed=make_embed("Unauthorized", "This menu is not for you."), ephemeral=True)
            return
        data = pending_deployments.get(self.admin_id)
        if not data:
            await interaction.response.send_message(embed=make_embed("Error", "Session expired. Run `.deploy` again."), ephemeral=True)
            return
        data["os"] = self.values[0]
        embed = make_embed(
            "Confirm Deployment",
            "Review the configuration below before deploying:",
            fields=[
                ("Target User",     data["target_user"].mention, True),
                ("Operating System", self.values[0],             True),
                ("RAM",             f"{data['ram']} GB",         True),
                ("CPU",             f"{data['cpu']} Core(s)",    True),
                ("Disk",            f"{data['disk']} GB",        True),
                ("Total",           f"${data['total']}/mo",      True),
            ],
        )
        await interaction.response.edit_message(embed=embed, view=ConfirmDeployView(self.admin_id))


class OSSelectView(discord.ui.View):
    def __init__(self, admin_id: int):
        super().__init__(timeout=120)
        self.add_item(OSSelect(admin_id))


class ConfirmDeployView(discord.ui.View):
    def __init__(self, admin_id: int):
        super().__init__(timeout=60)
        self.admin_id = admin_id

    @discord.ui.button(label="Deploy", style=discord.ButtonStyle.success, emoji="🚀")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(embed=make_embed("Unauthorized", "Not your deployment."), ephemeral=True)
            return
        data = pending_deployments.pop(self.admin_id, None)
        if not data:
            await interaction.response.send_message(embed=make_embed("Error", "Deployment data missing."), ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        msg = await interaction.followup.send(
            embed=make_embed("Deploying VPS...", f"Creating container for {data['target_user'].mention}. This may take a few minutes."),
            ephemeral=False,
        )
        asyncio.create_task(run_deployment(msg, data))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(embed=make_embed("Unauthorized", "Not your deployment."), ephemeral=True)
            return
        pending_deployments.pop(self.admin_id, None)
        await interaction.response.edit_message(embed=make_embed("Cancelled", "Deployment cancelled."), view=None)


async def run_deployment(status_msg: discord.Message, data: dict):
    target   = data["target_user"]
    ram_mb   = data["ram"] * 1024
    cpu      = data["cpu"]
    disk_gb  = data["disk"]
    os_val   = data["os"]
    os_name, os_version = os_val.split(":")
    ct_name  = f"vps-{target.id}"

    async def upd(title, desc):
        await status_msg.edit(embed=make_embed(title, desc))

    await upd("Step 1/5 — Creating Container", f"Running `lxc-create` for `{ct_name}` ({os_val})...")
    p = await asyncio.create_subprocess_exec(
        "sudo", "lxc-create", "-n", ct_name, "-t", "download",
        "--", "--dist", os_name, "--release", os_version, "--arch", "amd64",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await p.communicate()
    if p.returncode != 0:
        await upd("Deployment Failed", f"Container creation failed:\n```{err.decode()[:800]}```")
        return

    try:
        with open(f"/var/lib/lxc/{ct_name}/config", "a") as f:
            f.write(f"\nlxc.cgroup2.memory.max = {ram_mb}M\n")
            f.write(f"lxc.cgroup2.cpu.shares = {cpu * 1024}\n")
    except Exception:
        pass

    await upd("Step 2/5 — Starting Container", "Starting the LXC container...")
    p = await asyncio.create_subprocess_exec("sudo", "lxc-start", "-n", ct_name,
                                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, start_err = await p.communicate()
    if p.returncode != 0:
        await upd("Deployment Failed", f"Container failed to start:\n```{start_err.decode()[:800]}```")
        return
    await asyncio.sleep(3)

    await upd("Step 3/5 — Configuring DNS", "Setting DNS to 1.1.1.1 and 8.8.8.8...")
    await lxc_exec(ct_name, "echo 'nameserver 1.1.1.1\nnameserver 8.8.8.8' > /etc/resolv.conf")

    await upd("Step 4/5 — Updating System", "Running `apt update && apt upgrade`...")
    await lxc_exec(ct_name, "export DEBIAN_FRONTEND=noninteractive && apt-get update -y && apt-get upgrade -y")

    await upd("Step 5/5 — Installing Packages", "Installing tmate, openssh, curl, python3, wget, lsof...")
    await lxc_exec(ct_name, "export DEBIAN_FRONTEND=noninteractive && apt-get install -y tmate openssh-server curl python3 python3-pip python3-tk wget lsof")

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    db_set_vps(target.id, {
        "user_id":    target.id,
        "ct_name":    ct_name,
        "os":         os_val,
        "ram":        data["ram"],
        "cpu":        cpu,
        "disk":       disk_gb,
        "total":      data["total"],
        "created_at": created_at,
    })

    await status_msg.edit(embed=make_embed(
        "VPS Deployed Successfully ✅",
        f"Container for {target.mention} is live and ready.",
        fields=[
            ("Container",  f"`{ct_name}`",         True),
            ("OS",         os_val,                  True),
            ("Created",    created_at,              True),
            ("RAM",        f"{data['ram']} GB",     True),
            ("CPU",        f"{cpu} Core(s)",        True),
            ("Disk",       f"{disk_gb} GB",         True),
            ("Plan Cost",  f"${data['total']}/mo",  True),
            ("Packages",   "tmate, openssh, curl, python3, pip, python3-tk, wget, lsof", False),
        ],
    ))
    try:
        await target.send(embed=make_embed(
            "🚀 Your VPS is Ready!",
            "Your VPS has been deployed. Run `.manage` in the server to control it.",
            fields=[
                ("OS",      os_val,                 True),
                ("RAM",     f"{data['ram']} GB",    True),
                ("CPU",     f"{cpu} Core(s)",       True),
                ("Disk",    f"{disk_gb} GB",        True),
                ("Plan",    f"${data['total']}/mo", True),
                ("Created", created_at,             True),
            ],
        ))
    except discord.Forbidden:
        pass


class ResourceModalLauncher(discord.ui.View):
    def __init__(self, target_user: discord.Member):
        super().__init__(timeout=120)
        self.target_user = target_user

    @discord.ui.button(label="Set Resources", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "Only admins can use this."), ephemeral=True)
            return
        await interaction.response.send_modal(ResourceModal(self.target_user))


class DeleteConfirmView(discord.ui.View):
    def __init__(self, member: discord.Member, vps: dict):
        super().__init__(timeout=30)
        self.member = member
        self.vps    = vps

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(embed=make_embed("Unauthorized", "Only admins can confirm this."), ephemeral=True)
            return
        ct_name = self.vps["ct_name"]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        for cmd in [["sudo", "lxc-stop", "-n", ct_name, "-k"], ["sudo", "lxc-destroy", "-n", ct_name]]:
            p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await p.communicate()
        db_delete_vps(self.member.id)
        await interaction.followup.send(embed=make_embed("VPS Deleted 🗑️", f"Container `{ct_name}` destroyed and removed from the database."))
        try:
            await self.member.send(embed=make_embed("VPS Terminated", "Your VPS has been terminated by an administrator."))
        except discord.Forbidden:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=make_embed("Cancelled", "VPS deletion cancelled."), view=None)


@bot.command(name="maintenance")
async def maintenance(ctx, toggle: str = None):
    if ctx.author.id != MAIN_ADMIN_ID:
        await ctx.send(embed=make_embed("Permission Denied", "Only the main admin can toggle maintenance mode."))
        return
    if toggle not in ("on", "off"):
        await ctx.send(embed=make_embed("Usage Error", "Usage: `.maintenance on` or `.maintenance off`"))
        return
    meta = meta_load()
    meta["maintenance"] = (toggle == "on")
    meta_save(meta)
    state  = "🔴 Enabled" if meta["maintenance"] else "🟢 Disabled"
    desc   = (
        f"Maintenance mode has been **turned {toggle}** by {ctx.author.mention}.\n\n"
        + ("Users will no longer be able to access their VPS panel until maintenance is lifted." if toggle == "on"
           else "Users can now access their VPS panel normally.")
    )
    await ctx.send(embed=make_embed(f"🛠️ Maintenance Mode — {state}", desc))


@bot.command(name="addadmin")
async def addadmin(ctx, member: discord.Member = None):
    if ctx.author.id != MAIN_ADMIN_ID:
        await ctx.send(embed=make_embed("Permission Denied", "Only the main admin can add admins."))
        return
    if member is None:
        await ctx.send(embed=make_embed("Usage Error", "Usage: `.addadmin @user`"))
        return
    if member.id == MAIN_ADMIN_ID:
        await ctx.send(embed=make_embed("Already Main Admin", f"{member.mention} is the main admin."))
        return
    meta = meta_load()
    admins = [int(x) for x in meta.get("admins", [])]
    if member.id in admins:
        await ctx.send(embed=make_embed("Already Admin", f"{member.mention} is already an admin."))
        return
    admins.append(member.id)
    meta["admins"] = admins
    meta_save(meta)
    await ctx.send(embed=make_embed(
        "Admin Added ✅",
        f"{member.mention} has been granted admin access by {ctx.author.mention}.",
        fields=[("Permissions", "`.deploy` `.deletevps` `.manage @user` `.listvps` `.vpsinfo @user`", False)],
    ))
    try:
        await member.send(embed=make_embed(
            "🛡️ Admin Access Granted",
            f"You have been granted admin access on **Vortex Core** by {ctx.author.mention}.\n\n"
            "You can now use: `.deploy @user`, `.deletevps @user`, `.manage @user`, `.listvps`, `.vpsinfo @user`",
        ))
    except discord.Forbidden:
        pass


@bot.command(name="removeadmin")
async def removeadmin(ctx, member: discord.Member = None):
    if ctx.author.id != MAIN_ADMIN_ID:
        await ctx.send(embed=make_embed("Permission Denied", "Only the main admin can remove admins."))
        return
    if member is None:
        await ctx.send(embed=make_embed("Usage Error", "Usage: `.removeadmin @user`"))
        return
    meta   = meta_load()
    admins = [int(x) for x in meta.get("admins", [])]
    if member.id not in admins:
        await ctx.send(embed=make_embed("Not an Admin", f"{member.mention} is not a sub-admin."))
        return
    admins.remove(member.id)
    meta["admins"] = admins
    meta_save(meta)
    await ctx.send(embed=make_embed("Admin Removed", f"{member.mention}'s admin access has been revoked by {ctx.author.mention}."))
    try:
        await member.send(embed=make_embed("🛡️ Admin Access Revoked", "Your admin access on **Vortex Core** has been removed."))
    except discord.Forbidden:
        pass


@bot.command(name="listadmins")
async def listadmins(ctx):
    if ctx.author.id != MAIN_ADMIN_ID:
        await ctx.send(embed=make_embed("Permission Denied", "Only the main admin can view the admin list."))
        return
    meta   = meta_load()
    admins = [int(x) for x in meta.get("admins", [])]
    if not admins:
        await ctx.send(embed=make_embed("Admin List", f"No sub-admins. Only <@{MAIN_ADMIN_ID}> (main admin)."))
        return
    lines = [f"👑 <@{MAIN_ADMIN_ID}> — Main Admin"] + [f"🛡️ <@{uid}> — Sub-Admin" for uid in admins]
    await ctx.send(embed=make_embed(f"Admin List ({len(admins) + 1} total)", "\n".join(lines)))


@bot.command(name="deploy")
async def deploy(ctx, member: discord.Member = None):
    if not is_admin(ctx.author.id):
        await ctx.send(embed=make_embed("Permission Denied", "Only admins can deploy VPS instances."))
        return
    if member is None:
        await ctx.send(embed=make_embed("Usage Error", "Usage: `.deploy @user`"))
        return
    existing = db_get_vps(member.id)
    if existing:
        await ctx.send(embed=make_embed(
            "VPS Already Exists",
            f"{member.mention} already has a VPS (`{existing['ct_name']}`).\nUse `.deletevps @user` to remove it first.",
        ))
        return
    await ctx.send(
        embed=make_embed("Deploy VPS", f"Deploying a VPS for {member.mention}.\nClick below to configure resources."),
        view=ResourceModalLauncher(member),
    )


@bot.command(name="manage")
async def manage(ctx, member: discord.Member = None):
    if member is not None:
        if not is_admin(ctx.author.id):
            await ctx.send(embed=make_embed("Permission Denied", "Only admins can manage other users' VPS."))
            return
        vps = db_get_vps(member.id)
        if not vps:
            await ctx.send(embed=make_embed("No VPS Found", f"{member.mention} does not have an active VPS."))
            return
        embed, _ = await build_manage_embed(vps, member, admin_view=True)
        await ctx.send(embed=embed, view=AdminManageView(ctx.author.id, member.id, vps))
        return

    if is_maintenance() and not is_admin(ctx.author.id):
        await ctx.send(embed=make_embed(
            "🛠️ Maintenance Mode",
            "The VPS panel is currently unavailable due to maintenance.\nPlease check back later.",
        ))
        return

    vps = db_get_vps(ctx.author.id)
    if not vps:
        await ctx.send(embed=make_embed("No VPS Found", "You don't have an active VPS. Contact an admin to deploy one."))
        return
    embed, _ = await build_manage_embed(vps, ctx.author)
    await ctx.send(embed=embed, view=ManageView(ctx.author.id, vps))


@bot.command(name="vpsinfo")
async def vpsinfo(ctx, member: discord.Member = None):
    if member is not None and not is_admin(ctx.author.id):
        await ctx.send(embed=make_embed("Permission Denied", "Only admins can view other users' VPS info."))
        return
    target = member or ctx.author
    vps    = db_get_vps(target.id)
    if not vps:
        await ctx.send(embed=make_embed("No VPS Found", f"{target.mention} does not have an active VPS."))
        return
    status = await get_container_status(vps["ct_name"])
    await ctx.send(embed=make_embed(
        f"VPS Info — {target.display_name}", None,
        fields=[
            ("Status",    "🟢 Online" if status == "online" else "🔴 Offline", True),
            ("Container", f"`{vps['ct_name']}`",                               True),
            ("OS",        vps.get("os", "Unknown"),                            True),
            ("RAM",       f"{vps.get('ram','?')} GB",                          True),
            ("CPU",       f"{vps.get('cpu','?')} Core(s)",                     True),
            ("Disk",      f"{vps.get('disk','?')} GB",                         True),
            ("Plan",      f"${vps.get('total','?')}/mo",                       True),
            ("Created",   vps.get("created_at", "Unknown"),                    True),
        ],
    ))


@bot.command(name="listvps")
async def listvps(ctx):
    if not is_admin(ctx.author.id):
        await ctx.send(embed=make_embed("Permission Denied", "Only admins can list all VPS instances."))
        return
    data = db_load()
    if not data:
        await ctx.send(embed=make_embed("No VPS Instances", "There are no VPS containers in the database."))
        return
    lines = []
    for uid, vps in data.items():
        status = await get_container_status(vps["ct_name"])
        icon   = "🟢" if status == "online" else "🔴"
        lines.append(f"{icon} `{vps['ct_name']}` — <@{uid}> — {vps.get('os','?')} — ${vps.get('total','?')}/mo")
    await ctx.send(embed=make_embed(f"All VPS Instances ({len(data)})", "\n".join(lines)))


@bot.command(name="deletevps")
async def deletevps(ctx, member: discord.Member = None):
    if not is_admin(ctx.author.id):
        await ctx.send(embed=make_embed("Permission Denied", "Only admins can delete VPS instances."))
        return
    if member is None:
        await ctx.send(embed=make_embed("Usage Error", "Usage: `.deletevps @user`"))
        return
    vps = db_get_vps(member.id)
    if not vps:
        await ctx.send(embed=make_embed("Not Found", f"{member.mention} does not have a VPS."))
        return
    await ctx.send(embed=make_embed(
        "⚠️ Delete VPS",
        f"Permanently delete the VPS for {member.mention}?\n\n"
        f"Container: `{vps['ct_name']}`\nOS: {vps.get('os')}\n\n**This cannot be undone.**",
    ), view=DeleteConfirmView(member, vps))


@bot.command(name="help")
async def help_cmd(ctx):
    admin     = is_admin(ctx.author.id)
    main_adm  = ctx.author.id == MAIN_ADMIN_ID
    fields = [
        ("`.manage`",   "Open your VPS control panel — start, stop, restart, SSH, reinstall, stats, console, network", False),
        ("`.vpsinfo`",  "View your VPS specs and current status",                                                       False),
        ("`.help`",    "Show this help menu",                                                                          False),
    ]
    if admin:
        fields += [
            ("`.deploy @user`",   "Deploy a new VPS for a user",                              False),
            ("`.deletevps @user`","Permanently destroy a user's VPS",                         False),
            ("`.manage @user`",   "Open admin panel for a user's VPS (start/stop/SSH/stats)", False),
            ("`.listvps`",        "List all active VPS instances with status",                False),
            ("`.vpsinfo @user`",  "View any user's VPS details",                              False),
        ]
    if main_adm:
        fields += [
            ("`.maintenance on/off`", "Toggle maintenance mode (blocks user `.manage` access)", False),
            ("`.addadmin @user`",     "Grant a user sub-admin access",                          False),
            ("`.removeadmin @user`",  "Revoke a user's sub-admin access",                       False),
            ("`.listadmins`",         "Show all admins",                                        False),
        ]
    await ctx.send(embed=make_embed("Vortex Core — Command Help", "All available commands:", fields=fields))


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    db_load()
    meta_load()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Sync error: {e}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MemberNotFound):
        await ctx.send(embed=make_embed("Member Not Found", "Could not find that user. Make sure they are in this server."))
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=make_embed("Missing Argument", "You are missing a required argument. Try `.help` for usage."))
    elif isinstance(error, commands.CommandNotFound):
        pass


bot.run(TOKEN)
