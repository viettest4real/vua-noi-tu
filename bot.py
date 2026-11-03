import discord
from discord.ext import commands
import unicodedata
from collections import defaultdict
import random
import asyncio
import os
import json
import sys
import gc
from aiohttp import web

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ==========================================
# 0. CỔNG KEEP-ALIVE SIÊU NHẸ (CHO RENDER & CRON-JOB)
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot Noi Tu is online 24/7!")

async def start_keep_alive():
    try:
        app = web.Application()
        app.router.add_get("/", handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"🌐 Đã mở cổng Keep-Alive HTTP tại port {port}")
    except Exception as e:
        print(f"⚠️ Cổng Keep-Alive gặp sự cố: {e}")

# ==========================================
# 1. CHUẨN HÓA TIẾNG VIỆT & NẠP ĐỒ THỊ TỪ ĐIỂN
# ==========================================
TONE_MAP = {
    "oà": "òa", "oá": "óa", "oả": "ỏa", "oã": "õa", "oạ": "ọa",
    "oè": "òe", "oé": "óe", "oẻ": "ỏe", "oẽ": "õe", "oẹ": "ọe",
    "uỳ": "ùy", "uý": "úy", "uỷ": "ủy", "uỹ": "ũy", "uỵ": "ụy",
}

BLACKLIST_WORDS = {
    "tô nhượng", "ngựa tía", "úy lạo", "ủy lạo", "nậu nguồn", "a bung", "a bàng",
    "a dơi", "a giao", "a hành", "a hộ", "a lù", "a ngụy", "a phiến", "a tùng",
    "a túc", "a vương", "a xít", "a đảng", "a đới", "khâm sai", "ngự tiền"
}

CUSTOM_WORDS_FILE = "custom_words.txt"
VALID_WORDS = set()
WORD_GRAPH = defaultdict(set)

def clean_word(text: str) -> str:
    text = unicodedata.normalize("NFC", text.strip().lower())
    for old, new in TONE_MAP.items():
        text = text.replace(old, new)
    return " ".join(text.split())

def add_custom_word(word: str) -> bool:
    w = clean_word(word)
    parts = w.split()
    if len(parts) != 2:
        return False
    w1, w2 = sys.intern(parts[0]), sys.intern(parts[1])
    VALID_WORDS.add(sys.intern(f"{w1} {w2}"))
    WORD_GRAPH[w1].add(w2)
    try:
        with open(CUSTOM_WORDS_FILE, "a", encoding="utf-8") as f:
            f.write(w + "\n")
    except Exception as e:
        print(f"Lỗi lưu từ vào {CUSTOM_WORDS_FILE}: {e}")
    return True

def load_dictionary(filepath="words.txt"):
    VALID_WORDS.clear()
    WORD_GRAPH.clear()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                w = clean_word(line)
                if w in BLACKLIST_WORDS:
                    continue
                parts = w.split()
                if len(parts) == 2:
                    w1, w2 = sys.intern(parts[0]), sys.intern(parts[1])
                    VALID_WORDS.add(sys.intern(f"{w1} {w2}"))
                    WORD_GRAPH[w1].add(w2)
        print(f"✅ Đã nạp {len(VALID_WORDS)} từ gốc vào bộ nhớ đồ thị.")
    except FileNotFoundError:
        print("⚠️ Cảnh báo: Chưa có file words.txt! Hãy chạy 'py build_dict.py' trước.")

    if os.path.exists(CUSTOM_WORDS_FILE):
        try:
            extra_count = 0
            with open(CUSTOM_WORDS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    w = clean_word(line)
                    if w in BLACKLIST_WORDS:
                        continue
                    parts = w.split()
                    if len(parts) == 2:
                        w1, w2 = sys.intern(parts[0]), sys.intern(parts[1])
                        VALID_WORDS.add(sys.intern(f"{w1} {w2}"))
                        WORD_GRAPH[w1].add(w2)
                        extra_count += 1
            print(f"✅ Đã nạp thêm {extra_count} từ hiện đại từ {CUSTOM_WORDS_FILE}.")
        except Exception as e:
            print(f"⚠️ Lỗi đọc {CUSTOM_WORDS_FILE}: {e}")
    gc.collect()

def is_admin(user: discord.User | discord.Member) -> bool:
    if isinstance(user, discord.Member):
        return (
            user.guild_permissions.administrator
            or user.guild_permissions.manage_guild
            or user.guild_permissions.manage_messages
            or (user.guild.owner_id == user.id)
        )
    return False

# ==========================================
# 2. CẤU TRÚC BẢNG XẾP HẠNG & VÁN ĐẤU
# ==========================================
LEADERBOARD_FILE = "leaderboard.json"

def record_win(user_id: int, user_name: str, chain_length: int):
    data = {}
    if os.path.exists(LEADERBOARD_FILE):
        try:
            with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"name": user_name, "wins": 0, "max_chain": 0}
    data[uid]["wins"] += 1
    data[uid]["name"] = user_name
    if chain_length > data[uid].get("max_chain", 0):
        data[uid]["max_chain"] = chain_length
    try:
        with open(LEADERBOARD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Lỗi ghi leaderboard: {e}")

TURN_TIMEOUT = 30  # Thời gian suy nghĩ mỗi lượt ở chế độ Nhóm (giây)

class GameSession:
    def __init__(self, mode: str, channel: discord.TextChannel):
        self.mode = mode  # "PVP", "PVE", hoặc "GROUP"
        self.channel = channel
        self.active = (mode != "GROUP")  # GROUP cần chờ ở Sảnh (Lobby) trước khi active
        self.used_words = set()
        self.current_tail = None
        self.last_player_id = None
        self.chain_length = 0
        self.history: list[tuple[str, int]] = []  # Lưu lịch sử (từ, player_id) để hoàn tác

        # Thuộc tính riêng cho chế độ GROUP (Theo lượt)
        self.players: list[discord.Member] = []
        self.turn_index: int = 0
        self.timer_task: asyncio.Task | None = None

    @property
    def current_player(self) -> discord.Member | None:
        if self.players and 0 <= self.turn_index < len(self.players):
            return self.players[self.turn_index]
        return None

    def cancel_timer(self):
        if self.timer_task and not self.timer_task.done():
            self.timer_task.cancel()

    def start_turn_timer(self):
        self.cancel_timer()
        self.timer_task = asyncio.create_task(self._turn_timeout_handler())

    async def _turn_timeout_handler(self):
        """Trọng tài đếm ngược: Loại người chơi nếu quá 30 giây không nối được từ"""
        try:
            await asyncio.sleep(TURN_TIMEOUT)
            if not self.active or self.mode != "GROUP":
                return

            eliminated = self.current_player
            self.players.pop(self.turn_index)

            # Gửi thẻ báo bị loại
            embed_elim = discord.Embed(
                title="⏰ HẾT GIỜ — BỊ LOẠI TRỰC TIẾP!",
                color=0xED4245,
                description=(
                    f"### ❌ Người chơi bị loại:\n"
                    f"# 👤 {eliminated.mention}\n\n"
                    f"Đã quá **{TURN_TIMEOUT} giây** không nối được từ tiếp theo!"
                )
            )
            await self.channel.send(embed=embed_elim)

            # Nếu chỉ còn 1 người sống sót -> Vô địch
            if len(self.players) == 1:
                winner = self.players[0]
                self.active = False
                record_win(winner.id, winner.display_name, self.chain_length)

                embed_win = discord.Embed(
                    title="🏆 NHÀ VÔ ĐỊCH SINH TỒN!",
                    color=0xF1C40F,
                    description=(
                        f"# 👑 {winner.mention} LÀ NGƯỜI SỐNG SÓT CUỐI CÙNG!\n\n"
                        f"Chúc mừng bạn đã giành chiến thắng sau chuỗi **{self.chain_length}** từ!\n"
                        f"📊 Điểm chiến thắng đã được ghi danh vào Bảng Vàng (`!bxh`)."
                    )
                )
                await self.channel.send(embed=embed_win)
                games.pop(self.channel.id, None)
                return

            # Chuyển lượt cho người kế tiếp
            self.turn_index %= len(self.players)
            next_p = self.current_player

            embed_next = discord.Embed(
                title="🔄 LƯỢT NỐI TIẾP THEO",
                color=0xFEE75C,
                description=(
                    f"### 👉 Lượt tiếp theo thuộc về:\n"
                    f"# 👤 {next_p.mention}\n\n"
                    f"Hãy nối tiếp chữ: **`{self.current_tail} ...`**\n"
                    f"⏱️ Thời gian suy nghĩ: **{TURN_TIMEOUT} giây**"
                )
            )
            embed_next.set_footer(text=f"⏰ Quá {TURN_TIMEOUT}s sẽ bị loại trực tiếp!")
            await self.channel.send(embed=embed_next)
            self.start_turn_timer()

        except asyncio.CancelledError:
            pass

    def get_available_next_words(self, head_syllable: str) -> list[str]:
        candidates = []
        for tail in WORD_GRAPH.get(head_syllable, []):
            candidate = f"{head_syllable} {tail}"
            if candidate not in self.used_words and tail != head_syllable:
                candidates.append(candidate)
        return candidates

    def choose_bot_move(self, head_syllable: str) -> str | None:
        candidates = self.get_available_next_words(head_syllable)
        if not candidates:
            return None
        scored_moves = []
        for word in candidates:
            next_tail = word.split()[1]
            self.used_words.add(word)
            replies = len(self.get_available_next_words(next_tail))
            self.used_words.remove(word)
            scored_moves.append((replies, word))

        # Ưu tiên các từ quen thuộc/dễ thở (đối thủ có 2 - 8 cách đỡ)
        playable_moves = [w for replies, w in scored_moves if 2 <= replies <= 8]
        if playable_moves:
            return random.choice(playable_moves)

        # Nếu không có, chọn ngẫu nhiên từ trong nhóm có nhiều đường đỡ
        scored_moves.sort(key=lambda x: x[0], reverse=True)
        top_pool = [w for _, w in scored_moves[:5]]
        return random.choice(top_pool)

# ==========================================
# 3. GIAO DIỆN THẺ SẢNH CHỜ & DUYỆT TỪ
# ==========================================
class GroupLobbyView(discord.ui.View):
    def __init__(self, session: GameSession, host: discord.Member):
        super().__init__(timeout=300)
        self.session = session
        self.host = host

    def format_lobby_embed(self) -> discord.Embed:
        player_list = "\n".join(
            [f"`{idx + 1}.` {p.mention}" for idx, p in enumerate(self.session.players)]
        ) or "*Chưa có ai tham gia*"
        embed = discord.Embed(
            title="🏟️ SẢNH CHỜ NỐI TỪ THEO LƯỢT",
            color=0x9B59B6,
            description=(
                f"⏱️ **Luật chơi:** Chia lượt vòng tròn, mỗi lượt có **{TURN_TIMEOUT} giây** suy nghĩ.\n"
                f"Ai hết giờ sẽ bị trọng tài loại trực tiếp!\n\n"
                f"### 👥 Danh sách người chơi ({len(self.session.players)}):\n"
                f"{player_list}\n\n"
                f"*Bấm nút bên dưới để Tham gia / Rời phòng, hoặc gõ `!batdau` khi đã đủ người.*"
            )
        )
        embed.set_footer(text="Cần tối thiểu 2 người chơi để bắt đầu")
        return embed

    @discord.ui.button(label="Tham gia", style=discord.ButtonStyle.success, emoji="✋")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user in self.session.players:
            await interaction.response.send_message("Bạn đã có tên trong phòng rồi!", ephemeral=True)
            return
        self.session.players.append(interaction.user)
        await interaction.response.edit_message(embed=self.format_lobby_embed(), view=self)

    @discord.ui.button(label="Rời phòng", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.session.players:
            await interaction.response.send_message("Bạn chưa tham gia phòng này!", ephemeral=True)
            return
        self.session.players.remove(interaction.user)
        await interaction.response.edit_message(embed=self.format_lobby_embed(), view=self)

    @discord.ui.button(label="Bắt đầu ngay", style=discord.ButtonStyle.primary, emoji="🚀")
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.session.players) < 2:
            await interaction.response.send_message("⚠️ Cần ít nhất 2 người chơi để bắt đầu!", ephemeral=True)
            return
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await start_group_match(self.session)

class AdminApproveView(discord.ui.View):
    def __init__(self, word: str, session: GameSession, original_msg: discord.Message):
        super().__init__(timeout=120)
        self.word = word
        self.session = session
        self.original_msg = original_msg

    @discord.ui.button(label="Duyệt từ này (Quản trị viên)", style=discord.ButtonStyle.success, emoji="🛡️")
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("⛔ Chỉ Quản trị viên server mới có quyền duyệt từ!", ephemeral=True)
            return

        add_custom_word(self.word)
        self.stop()
        button.disabled = True
        button.label = f"Đã duyệt bởi {interaction.user.display_name}"

        embed_approved = discord.Embed(
            title="🛡️ TỪ ĐÃ ĐƯỢC PHÊ DUYỆT!",
            color=0x57F287,
            description=(
                f"# ✅ **`{self.word.upper()}`**\n\n"
                f"Quản trị viên {interaction.user.mention} đã phê duyệt từ này vào từ điển vĩnh viễn!\n"
                f"Trận đấu đang được tiếp tục..."
            )
        )
        await interaction.response.edit_message(embed=embed_approved, view=self)

        # Nếu ván đấu vẫn đang active và khớp với chữ đuôi lúc đó
        if self.session.active and (self.session.current_tail is None or self.word.startswith(self.session.current_tail)):
            try:
                await self.original_msg.add_reaction("✅")
                await self.original_msg.remove_reaction("❓", interaction.client.user)
            except Exception:
                pass
            await advance_turn(self.session, self.word, self.original_msg.author.id, self.original_msg)

async def start_group_match(session: GameSession):
    """Khởi động ván đấu nhóm, trộn thứ tự và chọn từ mở màn"""
    random.shuffle(session.players)
    session.active = True
    session.turn_index = 0

    valid_starters = [w for w in VALID_WORDS if len(WORD_GRAPH[w.split()[1]]) >= 5]
    first_word = random.choice(valid_starters) if valid_starters else random.choice(list(VALID_WORDS))
    session.used_words.add(first_word)
    session.current_tail = first_word.split()[1]
    session.chain_length = 1
    session.history.append((first_word, bot.user.id))

    order_str = " ➡️ ".join([p.mention for p in session.players])
    first_player = session.current_player

    embed = discord.Embed(
        title="🔔 TRẬN ĐẤU NHÓM BẮT ĐẦU!",
        color=0xFEE75C,
        description=(
            f"🔄 **Thứ tự vòng lượt:** {order_str}\n\n"
            f"### 📖 Từ mở màn của Trọng tài:\n"
            f"# 🎯 {first_word.upper()}\n\n"
            f"👉 Xin mời {first_player.mention} nối tiếp chữ **`{session.current_tail} ...`**\n"
            f"⏱️ Thời gian suy nghĩ: **{TURN_TIMEOUT} giây**"
        )
    )
    embed.set_footer(text=f"⏰ Quá {TURN_TIMEOUT}s không nối được sẽ bị loại trực tiếp!")
    await session.channel.send(embed=embed)
    session.start_turn_timer()

# ==========================================
# 4. KHỞI TẠO BOT & CÁC LỆNH ĐIỀU KHIỂN
# ==========================================
intents = discord.Intents.default()
intents.message_content = True

class NoiTuBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            case_insensitive=True,
            help_command=None,
            max_messages=None,  # Tắt lưu lịch sử tin nhắn trong RAM
            member_cache_flags=discord.MemberCacheFlags.none(),  # Tắt cache danh sách thành viên
            chunk_guilds_at_startup=False
        )

    async def setup_hook(self):
        await start_keep_alive()

bot = NoiTuBot()

games: dict[int, GameSession] = {}
last_rejected_word: dict[int, tuple[str, GameSession, discord.Message]] = {}

def stop_existing_game(channel_id: int):
    if channel_id in games:
        games[channel_id].cancel_timer()
        del games[channel_id]

@bot.event
async def on_ready():
    load_dictionary("words.txt")
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game(name="Nối từ | Gõ !pvp hoặc !pve")
    )
    print(f"🤖 Bot nối từ đã online dưới tên: {bot.user}")

@bot.command(name="pvp")
async def cmd_pvp(ctx):
    """Chế độ 1: Đấu tự do cho cả kênh"""
    stop_existing_game(ctx.channel.id)
    games[ctx.channel.id] = GameSession(mode="PVP", channel=ctx.channel)
    embed = discord.Embed(
        title="⚔️ ĐẠI CHIẾN NỐI TỪ (PvP Tự Do)",
        color=0x57F287,
        description=(
            f"# 🎮 SẴN SÀNG KHỞI TRANH!\n\n"
            f"Bất kỳ ai cũng có thể tham gia nối từ tiếp theo!\n"
            f"Hãy gõ **1 từ 2 âm tiết** bất kỳ để mở màn trận đấu.\n\n"
            f"*(Lưu ý: Không được tự nối chính mình 2 lần liên tiếp)*"
        )
    )
    embed.set_footer(text="🛑 Gõ !dung để dừng ván đấu bất cứ lúc nào")
    await ctx.send(embed=embed)

@bot.command(name="pve")
async def cmd_pve(ctx):
    """Chế độ 2: Đấu với Bot"""
    stop_existing_game(ctx.channel.id)
    session = GameSession(mode="PVE", channel=ctx.channel)
    games[ctx.channel.id] = session

    valid_starters = [w for w in VALID_WORDS if len(WORD_GRAPH[w.split()[1]]) >= 5]
    first_word = random.choice(valid_starters) if valid_starters else random.choice(list(VALID_WORDS))
    session.used_words.add(first_word)
    session.current_tail = first_word.split()[1]
    session.last_player_id = bot.user.id
    session.chain_length = 1
    session.history.append((first_word, bot.user.id))

    embed = discord.Embed(
        title="🤖 ĐẤU TRƯỜNG VỚI BOT (PvE)",
        color=0x5865F2,
        description=(
            f"### 🎯 Từ mở màn của Bot:\n"
            f"# 📖 {first_word.upper()}\n\n"
            f"👉 **Lượt của bạn:** Hãy nối tiếp chữ **`{session.current_tail} ...`**"
        )
    )
    embed.add_field(name="📊 Chuỗi từ", value="`1` từ", inline=True)
    embed.add_field(name="⏱️ Thời gian", value="Tự do", inline=True)
    embed.set_footer(text="💡 Gõ !hint để nhận gợi ý | 🏳️ Gõ !thua để xin hàng | 🔄 Gõ !reset để làm lại")
    await ctx.send(embed=embed)

@bot.command(name="nhom")
async def cmd_group(ctx):
    """Chế độ 3: Lập nhóm chơi theo lượt có Trọng tài đếm giờ"""
    stop_existing_game(ctx.channel.id)
    session = GameSession(mode="GROUP", channel=ctx.channel)
    session.players.append(ctx.author)
    games[ctx.channel.id] = session

    view = GroupLobbyView(session, ctx.author)
    await ctx.send(embed=view.format_lobby_embed(), view=view)

@bot.command(name="tg")
async def cmd_join(ctx):
    """Lệnh phụ để tham gia phòng bằng gõ chữ nếu không bấm nút"""
    session = games.get(ctx.channel.id)
    if not session or session.mode != "GROUP" or session.active:
        return
    if ctx.author not in session.players:
        session.players.append(ctx.author)
        embed = discord.Embed(
            title="✋ THAM GIA PHÒNG",
            color=0x57F287,
            description=f"✅ {ctx.author.mention} đã tham gia sảnh chờ! (Hiện có **{len(session.players)}** người)"
        )
        await ctx.send(embed=embed)

@bot.command(name="batdau")
async def cmd_start(ctx):
    """Lệnh phụ để bắt đầu ván nhóm bằng chữ"""
    session = games.get(ctx.channel.id)
    if not session or session.mode != "GROUP" or session.active:
        return
    if len(session.players) < 2:
        embed = discord.Embed(
            title="⚠️ CHƯA ĐỦ NGƯỜI",
            color=0xED4245,
            description="Cần tối thiểu **2 người chơi** để bắt đầu chia lượt vòng tròn!"
        )
        await ctx.send(embed=embed)
        return
    await start_group_match(session)

@bot.command(name="dung")
async def cmd_stop(ctx):
    if ctx.channel.id in games:
        stop_existing_game(ctx.channel.id)
        embed = discord.Embed(
            title="🛑 KẾT THÚC VÁN ĐẤU",
            color=0x95A5A6,
            description="Đã hủy và kết thúc ván nối từ hiện tại trong kênh này."
        )
        await ctx.send(embed=embed)

# --- CÁC TÍNH NĂNG MỞ RỘNG (DẠNG THẺ EMBED) ---

@bot.command(name="hint", aliases=["goiy", "gợiý"])
async def cmd_hint(ctx):
    """Gợi ý từ hợp lệ tiếp theo khi người chơi bí từ"""
    session = games.get(ctx.channel.id)
    if not session or not session.active:
        embed = discord.Embed(
            title="⚠️ CHƯA CÓ TRẬN ĐẤU",
            color=0xE67E22,
            description="Chưa có ván đấu nào đang diễn ra! Hãy gõ `!pve` hoặc `!pvp` để bắt đầu."
        )
        await ctx.send(embed=embed)
        return
    if not session.current_tail:
        embed = discord.Embed(
            title="💡 GỢI Ý",
            color=0xE67E22,
            description="Ván đấu chưa có từ mở màn, bạn có thể gõ bất kỳ từ 2 tiếng nào!"
        )
        await ctx.send(embed=embed)
        return
    options = session.get_available_next_words(session.current_tail)
    if not options:
        embed = discord.Embed(
            title="💀 HẾT ĐƯỜNG ĐỠ",
            color=0xED4245,
            description=f"Đã hết từ bắt đầu bằng **`{session.current_tail}`** trong từ điển rồi! Bạn có thể gõ `!thua` để kết thúc."
        )
        await ctx.send(embed=embed)
        return

    sample_size = min(3, len(options))
    hints = random.sample(options, sample_size)
    hint_items = "\n".join([f"# 🔹 {h.upper()}" for h in hints])

    embed = discord.Embed(
        title="💡 TRỢ GIÚP — GỢI Ý NƯỚC ĐI",
        color=0xE67E22,
        description=(
            f"### Các từ gợi ý cho chữ: **`{session.current_tail} ...`**\n\n"
            f"{hint_items}\n"
            f"*(Tổng cộng còn **{len(options)}** từ có thể đỡ)*"
        )
    )
    embed.set_footer(text="Nhanh tay gõ từ để tiếp tục ván đấu!")
    await ctx.send(embed=embed)

@bot.command(name="reset", aliases=["lamlai", "chơilại"])
async def cmd_reset(ctx):
    """Reset lại ván đấu hiện tại"""
    session = games.get(ctx.channel.id)
    if not session:
        embed = discord.Embed(
            title="⚠️ CHƯA CÓ VÁN ĐẤU",
            color=0xE67E22,
            description="Hiện không có ván đấu nào đang diễn ra trong kênh này!"
        )
        await ctx.send(embed=embed)
        return
    mode = session.mode
    stop_existing_game(ctx.channel.id)
    if mode == "PVE":
        await cmd_pve(ctx)
    elif mode == "PVP":
        await cmd_pvp(ctx)
    elif mode == "GROUP":
        await cmd_group(ctx)

@bot.command(name="thua", aliases=["surrender", "xinthua", "chịuthua"])
async def cmd_surrender(ctx):
    """Chấp nhận thua cuộc và kết thúc ván đấu"""
    session = games.get(ctx.channel.id)
    if not session or not session.active:
        embed = discord.Embed(
            title="⚠️ CHƯA CÓ VÁN ĐẤU",
            color=0xE67E22,
            description="Không có ván đấu nào đang diễn ra!"
        )
        await ctx.send(embed=embed)
        return

    session.cancel_timer()
    if session.mode == "PVE":
        embed = discord.Embed(
            title="🏁 ĐẦU HÀNG — KẾT QUẢ VÁN ĐẤU (PvE)",
            color=0xED4245,
            description=(
                f"# 🤖 BOT Ú NU GIÀNH CHIẾN THẮNG!\n\n"
                f"🏳️ Người đầu hàng: {ctx.author.mention}"
            )
        )
        embed.add_field(name="📊 Chuỗi từ đạt được", value=f"**{session.chain_length}** từ", inline=True)
        embed.set_footer(text="🔄 Gõ !pve hoặc !reset để phục thù ván mới!")
        await ctx.send(embed=embed)
        stop_existing_game(ctx.channel.id)

    elif session.mode == "PVP":
        embed = discord.Embed(
            title="🏁 KẾT THÚC VÁN ĐẤU (PvP)",
            color=0x95A5A6,
            description=(
                f"🏳️ {ctx.author.mention} đã xin dừng ván đấu!\n\n"
                f"📊 Chuỗi từ cả server đạt được: **{session.chain_length}** từ"
            )
        )
        embed.set_footer(text="🔄 Gõ !pvp để bắt đầu ván mới!")
        await ctx.send(embed=embed)
        stop_existing_game(ctx.channel.id)

    elif session.mode == "GROUP":
        if ctx.author in session.players:
            session.players.remove(ctx.author)
            embed_leave = discord.Embed(
                title="🏳️ RÚT LUI KHỎI TRẬN ĐẤU",
                color=0xE67E22,
                description=f"{ctx.author.mention} đã xin rút lui khỏi ván đấu nhóm!"
            )
            await ctx.send(embed=embed_leave)

            if len(session.players) == 1:
                winner = session.players[0]
                session.active = False
                record_win(winner.id, winner.display_name, session.chain_length)

                embed_win = discord.Embed(
                    title="🏆 NHÀ VÔ ĐỊCH SINH TỒN!",
                    color=0xF1C40F,
                    description=(
                        f"# 👑 {winner.mention} LÀ NGƯỜI SỐNG SÓT CUỐI CÙNG!\n\n"
                        f"Chúc mừng bạn đã giành chiến thắng sau chuỗi **{session.chain_length}** từ!\n"
                        f"📊 Điểm chiến thắng đã được ghi danh vào `!bxh`!"
                    )
                )
                await ctx.send(embed=embed_win)
                stop_existing_game(ctx.channel.id)
            else:
                session.turn_index %= len(session.players)
                next_p = session.current_player
                embed_next = discord.Embed(
                    title="🔄 LƯỢT NỐI TIẾP THEO",
                    color=0xFEE75C,
                    description=(
                        f"### 👉 Lượt tiếp theo thuộc về:\n"
                        f"# 👤 {next_p.mention}\n\n"
                        f"Nối tiếp chữ: **`{session.current_tail} ...`**\n"
                        f"⏱️ Thời gian: **{TURN_TIMEOUT} giây**"
                    )
                )
                await ctx.send(embed=embed_next)
                session.start_turn_timer()

@bot.command(name="duyet", aliases=["approve", "accept"])
async def cmd_duyet(ctx, *, custom_word: str = None):
    """Quản trị viên duyệt từ mới vào từ điển và tiếp tục ván đấu"""
    if not is_admin(ctx.author):
        embed = discord.Embed(
            title="⛔ TỪ CHỐI QUYỀN",
            color=0xED4245,
            description="Chỉ Quản trị viên server mới có quyền sử dụng lệnh này!"
        )
        await ctx.send(embed=embed, delete_after=5)
        return

    session = games.get(ctx.channel.id)
    target_word = None
    target_msg = None

    if custom_word:
        target_word = clean_word(custom_word)
    elif ctx.channel.id in last_rejected_word:
        target_word, prev_session, target_msg = last_rejected_word[ctx.channel.id]

    if not target_word:
        embed = discord.Embed(
            title="⚠️ THIẾU TỪ DUYỆT",
            color=0xE67E22,
            description="Hãy chỉ định từ cần duyệt, ví dụ: `!duyet đua xe` hoặc `!duyet nhượng quyền`"
        )
        await ctx.send(embed=embed, delete_after=5)
        return

    parts = target_word.split()
    if len(parts) != 2:
        embed = discord.Embed(
            title="⚠️ TỪ KHÔNG HỢP LỆ",
            color=0xED4245,
            description="Từ duyệt phải có đúng **2 âm tiết** (ví dụ: `đua xe`, `nhượng quyền`)!"
        )
        await ctx.send(embed=embed, delete_after=5)
        return

    add_custom_word(target_word)
    last_rejected_word.pop(ctx.channel.id, None)

    embed_ok = discord.Embed(
        title="🛡️ TỪ ĐÃ ĐƯỢC PHÊ DUYỆT!",
        color=0x57F287,
        description=(
            f"# ✅ **`{target_word.upper()}`**\n\n"
            f"Quản trị viên {ctx.author.mention} đã thêm từ này vào từ điển vĩnh viễn!"
        )
    )
    await ctx.send(embed=embed_ok)

    # Nếu đang có ván đấu và từ này khớp lượt đi:
    if session and session.active:
        if session.current_tail is None or parts[0] == session.current_tail:
            player_id = target_msg.author.id if target_msg else ctx.author.id
            if target_msg:
                try:
                    await target_msg.add_reaction("✅")
                    await target_msg.remove_reaction("❓", bot.user)
                except Exception:
                    pass
            await advance_turn(session, target_word, player_id, target_msg or ctx.message)

@bot.command(name="sua", aliases=["undo", "hoantac"])
async def cmd_undo(ctx):
    """Quản trị viên hoàn tác nước đi gần nhất nếu nối nhầm"""
    if not is_admin(ctx.author):
        embed = discord.Embed(
            title="⛔ TỪ CHỐI QUYỀN",
            color=0xED4245,
            description="Chỉ Quản trị viên server mới có quyền sử dụng lệnh này!"
        )
        await ctx.send(embed=embed, delete_after=5)
        return

    session = games.get(ctx.channel.id)
    if not session or not session.active or len(session.history) < 1:
        embed = discord.Embed(
            title="⚠️ KHÔNG THỂ HOÀN TÁC",
            color=0xE67E22,
            description="Không có nước đi nào trong lịch sử để hoàn tác!"
        )
        await ctx.send(embed=embed)
        return

    last_word, _ = session.history.pop()
    session.used_words.discard(last_word)
    session.chain_length = max(0, session.chain_length - 1)

    if session.history:
        prev_word, prev_player = session.history[-1]
        session.current_tail = prev_word.split()[1]
        session.last_player_id = prev_player
        embed = discord.Embed(
            title="↩️ HOÀN TÁC NƯỚC ĐI THÀNH CÔNG",
            color=0x3498DB,
            description=(
                f"Đã hủy bỏ từ: **`{last_word}`**\n\n"
                f"👉 **Chữ cần nối tiếp hiện tại:**\n"
                f"# 🎯 {session.current_tail.upper()} ..."
            )
        )
        await ctx.send(embed=embed)
    else:
        session.current_tail = None
        session.last_player_id = None
        embed = discord.Embed(
            title="↩️ HOÀN TÁC VỀ ĐẦU TRẬN",
            color=0x3498DB,
            description="# 🎮 BẮT ĐẦU LẠI TỪ ĐẦU!\n\nBạn có thể gõ bất kỳ từ 2 tiếng nào để mở màn."
        )
        await ctx.send(embed=embed)

# ==========================================
# 5. XỬ LÝ NỐI TỪ & TIẾP DIỄN VÁN ĐẤU (THẺ EMBED)
# ==========================================
async def advance_turn(session: GameSession, word: str, player_id: int, message: discord.Message):
    """Điều phối và chuyển lượt sau khi 1 từ được chấp nhận hợp lệ"""
    parts = word.split()
    session.used_words.add(word)
    session.current_tail = parts[1]
    session.last_player_id = player_id
    session.chain_length += 1
    session.history.append((word, player_id))

    # Kiểm tra xem từ vừa đánh có dồn vào thế bí không
    available_next = session.get_available_next_words(session.current_tail)
    if not available_next:
        session.cancel_timer()
        author_name = message.author.display_name if hasattr(message, "author") and hasattr(message.author, "display_name") else f"User {player_id}"
        record_win(player_id, author_name, session.chain_length)

        embed_win = discord.Embed(
            title="🏆 CHIẾN THẮNG TUYỆT ĐỐI!",
            color=0xF1C40F,
            description=(
                f"# 👑 NGƯỜI CHIẾN THẮNG: <@{player_id}>\n\n"
                f"💀 Không còn từ nào trong từ điển bắt đầu bằng **`{session.current_tail}`**!\n"
                f"📊 Chuỗi nối từ đạt kỷ lục: **{session.chain_length}** từ! *(Đã ghi danh vào `!bxh`)*"
            )
        )
        embed_win.set_footer(text="🔄 Gõ !pve hoặc !reset để chơi lại!")
        await message.channel.send(embed=embed_win)
        stop_existing_game(message.channel.id)
        return

    # --- ĐIỀU PHỐI LƯỢT TIẾP THEO TÙY CHẾ ĐỘ ---
    if session.mode == "GROUP":
        session.turn_index = (session.turn_index + 1) % len(session.players)
        next_p = session.current_player

        embed_next = discord.Embed(
            title="🔄 LƯỢT NỐI TIẾP THEO",
            color=0xFEE75C,
            description=(
                f"### 👉 Lượt tiếp theo thuộc về:\n"
                f"# 👤 {next_p.mention}\n\n"
                f"Hãy nối tiếp chữ: **`{session.current_tail} ...`**\n"
                f"⏱️ Thời gian suy nghĩ: **{TURN_TIMEOUT} giây**"
            )
        )
        embed_next.add_field(name="📊 Chuỗi từ", value=f"`{session.chain_length}` từ", inline=True)
        embed_next.set_footer(text=f"⏰ Quá {TURN_TIMEOUT}s sẽ bị loại trực tiếp!")
        await message.channel.send(embed=embed_next)
        session.start_turn_timer()

    elif session.mode == "PVE":
        bot_word = session.choose_bot_move(session.current_tail)
        if not bot_word:
            session.cancel_timer()
            author_name = message.author.display_name if hasattr(message, "author") and hasattr(message.author, "display_name") else f"User {player_id}"
            record_win(player_id, author_name, session.chain_length)

            embed_beat = discord.Embed(
                title="🏆 CHÚC MỪNG BẠN ĐÃ THẮNG BOT!",
                color=0xF1C40F,
                description=(
                    f"# 👑 NGƯỜI CHIẾN THẮNG: <@{player_id}>\n\n"
                    f"🤖 Bot Ú Nu đã chịu thua vì không còn từ nào đỡ cho chữ **`{session.current_tail}`**!\n"
                    f"📊 Tổng chuỗi từ: **{session.chain_length}** từ! *(Đã ghi danh vào `!bxh`)*"
                )
            )
            embed_beat.set_footer(text="🔄 Gõ !pve hoặc !reset để chơi ván mới!")
            await message.channel.send(embed=embed_beat)
            stop_existing_game(message.channel.id)
            return

        session.used_words.add(bot_word)
        session.current_tail = bot_word.split()[1]
        session.last_player_id = bot.user.id
        session.chain_length += 1
        session.history.append((bot_word, bot.user.id))

        player_options = session.get_available_next_words(session.current_tail)
        if not player_options:
            embed_lose = discord.Embed(
                title="💀 HẾT ĐƯỜNG ĐỠ — BOT CHIẾN THẮNG!",
                color=0xED4245,
                description=(
                    f"### Bot ra đòn kết liễu:\n"
                    f"# 🎯 {bot_word.upper()}\n\n"
                    f"💀 Không còn từ nào trong từ điển bắt đầu bằng chữ **`{session.current_tail}`**!"
                )
            )
            embed_lose.add_field(name="🤖 Người chiến thắng", value="Bot Ú Nu", inline=True)
            embed_lose.add_field(name="📊 Chuỗi từ dừng ở", value=f"`{session.chain_length}` từ", inline=True)
            embed_lose.set_footer(text="🔄 Gõ !pve hoặc !reset để phục thù!")
            await message.channel.send(embed=embed_lose)
            stop_existing_game(message.channel.id)
        else:
            embed_bot = discord.Embed(
                title="🤖 LƯỢT ĐÁP TRẢ CỦA BOT",
                color=0x3498DB,
                description=(
                    f"# 🎯 {bot_word.upper()}\n\n"
                    f"👉 **Lượt của bạn:** Hãy nối tiếp chữ **`{session.current_tail} ...`**"
                )
            )
            embed_bot.add_field(name="📊 Chuỗi từ", value=f"`{session.chain_length}` từ", inline=True)
            embed_bot.add_field(name="🛤️ Đường đỡ khả dụng", value=f"`{len(player_options)}` cách", inline=True)
            embed_bot.set_footer(text="💡 Gõ !hint để gợi ý | 🏳️ Gõ !thua để xin hàng | 🔄 !reset")
            await message.channel.send(embed=embed_bot)

@bot.command(name="help", aliases=["huongdan", "luat", "menu"])
async def cmd_help(ctx):
    """Bảng hướng dẫn cách chơi và danh sách lệnh dạng Thẻ"""
    embed = discord.Embed(
        title="🎮 HƯỚNG DẪN CHƠI BOT NỐI TỪ (Ú NU - VUA LỐI TỪ)",
        color=0x5865F2,
        description="Chào mừng bạn đến với đấu trường nối từ tiếng Việt 2 âm tiết!"
    )
    embed.add_field(
        name="⚔️ Các chế độ chơi",
        value=(
            "• **`!pve`** : Đấu 1-1 với Bot (Bot ra từ tự nhiên, không chơi bẩn).\n"
            "• **`!pvp`** : Đấu tự do cả server (luân phiên nối tiếp nhau).\n"
            "• **`!nhom`**: Đấu trường sinh tồn đếm ngược 30s/lượt."
        ),
        inline=False
    )
    embed.add_field(
        name="💡 Câu lệnh hỗ trợ trong trận",
        value=(
            "• **`!hint`** : Gợi ý 1–3 từ hợp lệ khi bạn bí từ.\n"
            "• **`!reset`**: Khởi động lại ván đấu ngay lập tức.\n"
            "• **`!thua`** : Xin hàng và xem tổng kết ván đấu.\n"
            "• **`!dung`** : Dừng hẳn ván đấu hiện tại trong kênh.\n"
            "• **`!bxh`** : Xem Bảng Vàng Top 10 cao thủ server."
        ),
        inline=False
    )
    embed.add_field(
        name="🛡️ Dành cho Quản trị viên (Admin)",
        value=(
            "• **Nút [Duyệt từ này]**: Khi bot gặp từ lạ, Admin bấm nút để thêm vĩnh viễn.\n"
            "• **`!duyet <từ>`**: Duyệt thêm bất kỳ từ mới nào vào từ điển.\n"
            "• **`!sua`** : Hoàn tác 1 nước đi vừa nối nếu lỡ tay gõ nhầm."
        ),
        inline=False
    )
    embed.set_footer(text="📌 Luật chơi: Nối từ ghép đúng 2 âm tiết (VD: đua xe ➡️ xe máy ➡️ máy tính)")
    await ctx.send(embed=embed)

@bot.command(name="bxh", aliases=["top", "xephang"])
async def cmd_leaderboard(ctx):
    """Xem bảng xếp hạng cao thủ nối từ dạng Thẻ"""
    if not os.path.exists(LEADERBOARD_FILE):
        embed = discord.Embed(
            title="📊 BẢNG VÀNG CAO THỦ",
            color=0xF1C40F,
            description="Chưa có dữ liệu bảng xếp hạng! Hãy chơi `!pve` hoặc `!nhom` để ghi danh nhé."
        )
        await ctx.send(embed=embed)
        return
    try:
        with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not data:
        embed = discord.Embed(
            title="📊 BẢNG VÀNG CAO THỦ",
            color=0xF1C40F,
            description="Bảng xếp hạng đang trống! Hãy làm ván đấu đầu tiên nào."
        )
        await ctx.send(embed=embed)
        return

    sorted_users = sorted(data.values(), key=lambda x: (x.get("wins", 0), x.get("max_chain", 0)), reverse=True)[:10]

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for idx, entry in enumerate(sorted_users):
        prefix = medals[idx] if idx < 3 else f"`#{idx + 1}`"
        name = entry.get("name", "Người chơi")
        wins = entry.get("wins", 0)
        max_chain = entry.get("max_chain", 0)
        lines.append(f"{prefix} **{name}** — 🏆 **{wins}** trận thắng *(Chuỗi kỷ lục: {max_chain} từ)*")

    embed = discord.Embed(
        title="👑 BẢNG VÀNG CAO THỦ NỐI TỪ SERVER",
        color=0xF1C40F,
        description="\n\n".join(lines)
    )
    embed.set_footer(text="Chiến thắng ở chế độ !pve hoặc !nhom để ghi danh vào Bảng Vàng!")
    await ctx.send(embed=embed)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.content.startswith("!"):
        await bot.process_commands(message)
        return

    session = games.get(message.channel.id)
    if not session or not session.active:
        return

    word = clean_word(message.content)
    parts = word.split()
    if len(parts) != 2:
        return

    # --- KIỂM TRA LƯỢT CHƠI ---
    if session.mode == "GROUP":
        if message.author != session.current_player:
            await message.add_reaction("⛔")
            await message.reply(
                f"Chưa tới lượt bạn! Hiện đang là lượt của {session.current_player.mention}.",
                delete_after=3
            )
            return
    elif session.mode == "PVP":
        if message.author.id == session.last_player_id:
            await message.add_reaction("⏳")
            await message.reply("Bạn vừa nối rồi, hãy nhường lượt cho người tiếp theo!", delete_after=3)
            return

    # --- KIỂM TRA LUẬT NỐI TỪ ---
    if session.current_tail and parts[0] != session.current_tail:
        await message.add_reaction("❌")
        await message.reply(f"Sai chữ đầu! Phải bắt đầu bằng **`{session.current_tail}`**.", delete_after=3)
        return

    if word in session.used_words:
        await message.add_reaction("♻️")
        await message.reply(f"Từ **`{word}`** đã được sử dụng rồi!", delete_after=3)
        return

    if word not in VALID_WORDS:
        await message.add_reaction("❓")
        last_rejected_word[message.channel.id] = (word, session, message)
        view = AdminApproveView(word, session, message)
        embed_reject = discord.Embed(
            title="❓ TỪ CHƯA CÓ TRONG TỪ ĐIỂN",
            color=0xE67E22,
            description=(
                f"# ⚠️ **`{word.upper()}`**\n\n"
                f"Từ này hiện chưa có trong kho dữ liệu từ điển.\n"
                f"*(Quản trị viên có thể bấm nút bên dưới hoặc gõ `!duyet` để chấp nhận và tiếp tục)*"
            )
        )
        await message.reply(embed=embed_reject, view=view)
        return

    # --- TỪ HỢP LỆ ---
    await message.add_reaction("✅")
    await advance_turn(session, word, message.author.id, message)

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    # Tự động đọc file .env nếu không có thư viện dotenv
    if not os.environ.get("DISCORD_TOKEN") and os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("DISCORD_TOKEN="):
                        os.environ["DISCORD_TOKEN"] = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                    elif line.strip().startswith("PORT="):
                        os.environ["PORT"] = line.strip().split("=", 1)[1].strip()
        except Exception:
            pass

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise ValueError("Chưa cấu hình biến môi trường DISCORD_TOKEN! Vui lòng cấu hình trong file .env hoặc trên Render.")
    bot.run(token)
