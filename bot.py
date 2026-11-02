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
    VALID_WORDS.add(w)
    WORD_GRAPH[parts[0]].add(parts[1])
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
# 2. CẤU TRÚC QUẢN LÝ VÁN ĐẤU & TRỌNG TÀI
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

TURN_TIMEOUT = 30  # Thời gian suy nghĩ tối đa mỗi lượt ở chế độ Nhóm (giây)

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

            await self.channel.send(
                f"⏰ **HẾT GIỜ!** {eliminated.mention} đã quá {TURN_TIMEOUT}s không nối được từ và **BỊ LOẠI**!"
            )

            # Nếu chỉ còn 1 người sống sót -> Vô địch
            if len(self.players) == 1:
                winner = self.players[0]
                self.active = False
                record_win(winner.id, winner.display_name, self.chain_length)
                await self.channel.send(
                    f"🏆 **NHÀ VÔ ĐỊCH!** Chúc mừng {winner.mention} là người sống sót cuối cùng sau chuỗi **{self.chain_length}** từ!\n"
                    f"📊 Điểm chiến thắng đã được ghi danh vào `!bxh`!"
                )
                games.pop(self.channel.id, None)
                return

            # Chuyển lượt cho người kế tiếp
            self.turn_index %= len(self.players)
            next_p = self.current_player
            await self.channel.send(
                f"👉 Lượt tiếp theo thuộc về {next_p.mention}! Hãy nối tiếp chữ **`{self.current_tail} ...`** ({TURN_TIMEOUT}s)"
            )
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
# 3. GIAO DIỆN NÚT BẤM SẢNH CHỜ & DUYỆT TỪ
# ==========================================
class GroupLobbyView(discord.ui.View):
    def __init__(self, session: GameSession, host: discord.Member):
        super().__init__(timeout=300)
        self.session = session
        self.host = host

    def format_lobby_message(self) -> str:
        player_list = "\n".join(
            [f"{idx + 1}. {p.mention}" for idx, p in enumerate(self.session.players)]
        )
        return (
            f"🏟️ **SẢNH CHỜ NỐI TỪ THEO LƯỢT (Trọng tài Bot)**\n"
            f"⏱️ Luật chơi: Chia lượt vòng tròn, mỗi lượt có **{TURN_TIMEOUT} giây** suy nghĩ. Ai hết giờ sẽ bị loại trực tiếp!\n\n"
            f"👥 **Danh sách người chơi ({len(self.session.players)}):**\n{player_list}\n\n"
            f"*Bấm nút bên dưới để Tham gia / Rời phòng, hoặc gõ `!batdau` khi đã đủ người.*"
        )

    @discord.ui.button(label="Tham gia", style=discord.ButtonStyle.success, emoji="✋")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user in self.session.players:
            await interaction.response.send_message("Bạn đã có tên trong phòng rồi!", ephemeral=True)
            return
        self.session.players.append(interaction.user)
        await interaction.response.edit_message(content=self.format_lobby_message(), view=self)

    @discord.ui.button(label="Rời phòng", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.session.players:
            await interaction.response.send_message("Bạn chưa tham gia phòng này!", ephemeral=True)
            return
        self.session.players.remove(interaction.user)
        await interaction.response.edit_message(content=self.format_lobby_message(), view=self)

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
        await interaction.response.edit_message(
            content=f"✅ **Quản trị viên {interaction.user.mention} đã phê duyệt từ `{self.word}`!** Từ này đã được thêm vào từ điển vĩnh viễn.",
            view=self
        )

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

    await session.channel.send(
        f"🔔 **TRẬN ĐẤU BẮT ĐẦU!**\n"
        f"🔄 **Thứ tự vòng lượt:** {order_str}\n"
        f"📖 **Từ mở màn của Trọng tài:** **`{first_word}`**\n\n"
        f"👉 Xin mời {first_player.mention} nối tiếp bằng chữ **`{session.current_tail} ...`** (Bạn có {TURN_TIMEOUT}s)"
    )
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
    await ctx.send("⚔️ **Chế độ PvP Tự Do đã bật!** Ai cũng có thể nối (không tự nối 2 lần liên tiếp). Hãy gõ 1 từ 2 âm tiết bất kỳ để mở màn!")

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

    await ctx.send(
        f"🤖 **Chế độ Đấu với Bot (PvE)!**\n"
        f"Từ mở màn của tôi là: **`{first_word}`** ➡️ Hãy nối tiếp chữ **`{session.current_tail} ...`**\n"
        f"*(Gõ `!hint` nếu bí từ, gõ `!thua` để xin hàng, gõ `!reset` để làm lại)*"
    )

@bot.command(name="nhom")
async def cmd_group(ctx):
    """Chế độ 3: Lập nhóm chơi theo lượt có Trọng tài đếm giờ"""
    stop_existing_game(ctx.channel.id)
    session = GameSession(mode="GROUP", channel=ctx.channel)
    session.players.append(ctx.author)
    games[ctx.channel.id] = session

    view = GroupLobbyView(session, ctx.author)
    await ctx.send(content=view.format_lobby_message(), view=view)

@bot.command(name="tg")
async def cmd_join(ctx):
    """Lệnh phụ để tham gia phòng bằng gõ chữ nếu không bấm nút"""
    session = games.get(ctx.channel.id)
    if not session or session.mode != "GROUP" or session.active:
        return
    if ctx.author not in session.players:
        session.players.append(ctx.author)
        await ctx.send(f"✅ {ctx.author.mention} đã tham gia! (Hiện có {len(session.players)} người)")

@bot.command(name="batdau")
async def cmd_start(ctx):
    """Lệnh phụ để bắt đầu ván nhóm bằng chữ"""
    session = games.get(ctx.channel.id)
    if not session or session.mode != "GROUP" or session.active:
        return
    if len(session.players) < 2:
        await ctx.send("⚠️ Cần tối thiểu 2 người tham gia để bắt đầu chia lượt!")
        return
    await start_group_match(session)

@bot.command(name="dung")
async def cmd_stop(ctx):
    if ctx.channel.id in games:
        stop_existing_game(ctx.channel.id)
        await ctx.send("🛑 Đã kết thúc ván nối từ hiện tại.")

# --- CÁC TÍNH NĂNG MỚI ĐƯỢC BỔ SUNG ---

@bot.command(name="hint", aliases=["goiy", "gợiý"])
async def cmd_hint(ctx):
    """Gợi ý từ hợp lệ tiếp theo khi người chơi bí từ"""
    session = games.get(ctx.channel.id)
    if not session or not session.active:
        await ctx.send("⚠️ Chưa có ván đấu nào đang diễn ra! Gõ `!pve` hoặc `!pvp` để bắt đầu.")
        return
    if not session.current_tail:
        await ctx.send("💡 Ván đấu chưa có từ mở màn, bạn có thể gõ bất kỳ từ 2 tiếng nào!")
        return
    options = session.get_available_next_words(session.current_tail)
    if not options:
        await ctx.send(f"💀 Đã hết từ bắt đầu bằng **`{session.current_tail}`** trong từ điển rồi! Bạn có thể gõ `!thua` để kết thúc.")
        return
    sample_size = min(3, len(options))
    hints = random.sample(options, sample_size)
    hint_str = ", ".join([f"**`{h}`**" for h in hints])
    await ctx.send(f"💡 **Gợi ý cho chữ `{session.current_tail} ...`**: {hint_str} *(Còn {len(options)} từ có thể đỡ)*")

@bot.command(name="reset", aliases=["lamlai", "chơilại"])
async def cmd_reset(ctx):
    """Reset lại ván đấu hiện tại"""
    session = games.get(ctx.channel.id)
    if not session:
        await ctx.send("⚠️ Hiện không có ván đấu nào đang diễn ra trong kênh này!")
        return
    mode = session.mode
    stop_existing_game(ctx.channel.id)
    await ctx.send("🔄 **Đã reset ván đấu! Bắt đầu lại ván mới...**")
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
        await ctx.send("⚠️ Không có ván đấu nào đang diễn ra!")
        return

    session.cancel_timer()
    if session.mode == "PVE":
        await ctx.send(
            f"🏁 =================================== 🏁\n"
            f"🏆 **KẾT QUẢ VÁN ĐẤU (PvE)**\n"
            f"🤖 **Người chiến thắng:** Bot Ú Nu\n"
            f"🏳️ **Người đầu hàng:** {ctx.author.mention}\n"
            f"📊 **Chuỗi từ đạt được:** **{session.chain_length}** từ\n"
            f"🔄 Gõ `!pve` hoặc `!reset` để phục thù!\n"
            f"🏁 =================================== 🏁"
        )
        stop_existing_game(ctx.channel.id)
    elif session.mode == "PVP":
        await ctx.send(
            f"🏁 =================================== 🏁\n"
            f"🏆 **KẾT THÚC VÁN ĐẤU (PvP)**\n"
            f"🏳️ {ctx.author.mention} đã xin thua!\n"
            f"📊 **Chuỗi từ đạt được:** **{session.chain_length}** từ\n"
            f"🔄 Gõ `!pvp` để bắt đầu ván mới!\n"
            f"🏁 =================================== 🏁"
        )
        stop_existing_game(ctx.channel.id)
    elif session.mode == "GROUP":
        if ctx.author in session.players:
            session.players.remove(ctx.author)
            await ctx.send(f"🏳️ {ctx.author.mention} đã xin rút lui khỏi ván đấu!")
            if len(session.players) == 1:
                winner = session.players[0]
                session.active = False
                await ctx.send(
                    f"🏆 **NHÀ VÔ ĐỊCH!** Chúc mừng {winner.mention} đã giành chiến thắng sau chuỗi **{session.chain_length}** từ!"
                )
                stop_existing_game(ctx.channel.id)
            else:
                session.turn_index %= len(session.players)
                next_p = session.current_player
                await ctx.send(f"👉 Lượt tiếp theo thuộc về {next_p.mention}! Nối tiếp chữ **`{session.current_tail} ...`** ({TURN_TIMEOUT}s)")
                session.start_turn_timer()

@bot.command(name="duyet", aliases=["approve", "accept"])
async def cmd_duyet(ctx, *, custom_word: str = None):
    """Quản trị viên duyệt từ mới vào từ điển và tiếp tục ván đấu"""
    if not is_admin(ctx.author):
        await ctx.send("⛔ Chỉ Quản trị viên server mới có quyền sử dụng lệnh này!", delete_after=5)
        return

    session = games.get(ctx.channel.id)
    target_word = None
    target_msg = None

    if custom_word:
        target_word = clean_word(custom_word)
    elif ctx.channel.id in last_rejected_word:
        target_word, prev_session, target_msg = last_rejected_word[ctx.channel.id]

    if not target_word:
        await ctx.send("⚠️ Hãy chỉ định từ cần duyệt, ví dụ: `!duyet đua xe` hoặc `!duyet nhượng quyền`", delete_after=5)
        return

    parts = target_word.split()
    if len(parts) != 2:
        await ctx.send("⚠️ Từ duyệt phải có đúng 2 âm tiết (ví dụ: `đua xe`, `nhượng quyền`)!", delete_after=5)
        return

    add_custom_word(target_word)
    last_rejected_word.pop(ctx.channel.id, None)

    await ctx.send(f"🛡️ **Quản trị viên {ctx.author.mention} đã phê duyệt từ `{target_word}` vào từ điển vĩnh viễn!**")

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
        await ctx.send("⛔ Chỉ Quản trị viên server mới có quyền sử dụng lệnh này!", delete_after=5)
        return

    session = games.get(ctx.channel.id)
    if not session or not session.active or len(session.history) < 1:
        await ctx.send("⚠️ Không có nước đi nào để hoàn tác!")
        return

    last_word, _ = session.history.pop()
    session.used_words.discard(last_word)
    session.chain_length = max(0, session.chain_length - 1)

    if session.history:
        prev_word, prev_player = session.history[-1]
        session.current_tail = prev_word.split()[1]
        session.last_player_id = prev_player
        await ctx.send(f"↩️ **Đã hoàn tác từ `{last_word}`.** Chữ cần nối tiếp hiện tại là: **`{session.current_tail} ...`**")
    else:
        session.current_tail = None
        session.last_player_id = None
        await ctx.send(f"↩️ **Đã hoàn tác về đầu ván.** Bạn có thể gõ bất kỳ từ 2 tiếng nào để bắt đầu!")

# ==========================================
# 5. XỬ LÝ NỐI TỪ & TIẾP DIỄN VÁN ĐẤU
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
        await message.channel.send(
            f"🏁 =================================== 🏁\n"
            f"🏆 **TUYỆT ĐỐI! CHIẾN THẮNG!**\n"
            f"👑 Người chiến thắng: <@{player_id}>\n"
            f"💀 Không còn từ nào bắt đầu bằng **`{session.current_tail}`** trong từ điển.\n"
            f"📊 Chuỗi nối từ đạt kỷ lục: **{session.chain_length}** từ! (Đã ghi danh vào `!bxh`)\n"
            f"🏁 =================================== 🏁"
        )
        stop_existing_game(message.channel.id)
        return

    # --- ĐIỀU PHỐI LƯỢT TIẾP THEO TÙY CHẾ ĐỘ ---
    if session.mode == "GROUP":
        session.turn_index = (session.turn_index + 1) % len(session.players)
        next_p = session.current_player
        await message.channel.send(
            f"👉 Lượt của {next_p.mention}: Nối tiếp **`{session.current_tail} ...`** ({TURN_TIMEOUT}s)"
        )
        session.start_turn_timer()

    elif session.mode == "PVE":
        bot_word = session.choose_bot_move(session.current_tail)
        if not bot_word:
            session.cancel_timer()
            author_name = message.author.display_name if hasattr(message, "author") and hasattr(message.author, "display_name") else f"User {player_id}"
            record_win(player_id, author_name, session.chain_length)
            await message.channel.send(
                f"🏁 =================================== 🏁\n"
                f"🏆 **CHÚC MỪNG BẠN ĐÃ THẮNG BOT!**\n"
                f"👑 Người chiến thắng: <@{player_id}>\n"
                f"🤖 Bot chịu thua vì không còn từ nào đỡ cho chữ **`{session.current_tail}`**!\n"
                f"📊 Tổng chuỗi từ: **{session.chain_length}** từ! (Đã ghi danh vào `!bxh`)\n"
                f"🔄 Gõ `!pve` hoặc `!reset` để chơi lại!\n"
                f"🏁 =================================== 🏁"
            )
            stop_existing_game(message.channel.id)
            return

        session.used_words.add(bot_word)
        session.current_tail = bot_word.split()[1]
        session.last_player_id = bot.user.id
        session.chain_length += 1
        session.history.append((bot_word, bot.user.id))

        player_options = session.get_available_next_words(session.current_tail)
        if not player_options:
            await message.channel.send(
                f"🤖 Bot nối: **`{bot_word}`**\n\n"
                f"🏁 =================================== 🏁\n"
                f"💀 **BOT GIÀNH CHIẾN THẮNG!**\n"
                f"🤖 Người thắng: Bot Ú Nu\n"
                f"💔 Không còn từ nào đỡ được cho chữ **`{session.current_tail}`**.\n"
                f"📊 Chuỗi từ dừng lại ở: **{session.chain_length}** từ.\n"
                f"🔄 Gõ `!pve` hoặc `!reset` để phục thù!\n"
                f"🏁 =================================== 🏁"
            )
            stop_existing_game(message.channel.id)
        else:
            await message.channel.send(
                f"🤖 **`{bot_word}`** (Còn `{len(player_options)}` cách đỡ cho chữ `{session.current_tail} ...` - Gõ `!hint` nếu bí)"
            )

@bot.command(name="help", aliases=["huongdan", "luat", "menu"])
async def cmd_help(ctx):
    """Bảng hướng dẫn cách chơi và danh sách lệnh"""
    msg = (
        "📖 ========================================== 📖\n"
        "🎮 **HƯỚNG DẪN CHƠI BOT NỐI TỪ (Ú NU - VUA LỐI TỪ)** 🎮\n"
        "📖 ========================================== 📖\n\n"
        "⚔️ **CÁC CHẾ ĐỘ CHƠI:**\n"
        "• **`!pve`** : Đấu 1-1 với Bot (Bot ra từ tự nhiên, không chơi bẩn).\n"
        "• **`!pvp`** : Đấu tự do cả server (luân phiên nối, không tự nối 2 lần liên tiếp).\n"
        "• **`!nhom`**: Tạo phòng đấu loại trực tiếp có đếm giờ (30s/lượt), người sống sót cuối cùng vô địch!\n\n"
        "💡 **CÂU LỆNH HỖ TRỢ TRONG VÁN ĐẤU:**\n"
        "• **`!hint`** *(hoặc `!goiy`)*: Gợi ý 1-3 từ hợp lệ khi bạn bí từ.\n"
        "• **`!reset`** *(hoặc `!lamlai`)*: Khởi động lại ván đấu ngay lập tức.\n"
        "• **`!thua`** : Xin hàng và kết thúc ván đấu, hiển thị bảng kết quả.\n"
        "• **`!dung`** : Dừng hẳn ván đấu hiện tại trong kênh.\n"
        "• **`!bxh`** *(hoặc `!top`)*: Xem Bảng Vàng Cao Thủ Nối Từ của server.\n\n"
        "🛡️ **DÀNH CHO QUẢN TRỊ VIÊN (ADMIN):**\n"
        "• **Nút [Duyệt từ này]**: Khi bot gặp từ lạ, Admin bấm nút để thêm vĩnh viễn vào từ điển.\n"
        "• **`!duyet <từ>`**: Duyệt thêm bất kỳ từ mới nào vào từ điển.\n"
        "• **`!sua`** *(hoặc `!undo`)*: Hoàn tác 1 nước đi vừa nối nếu lỡ tay nối nhầm.\n"
        "──────────────────────────────────────────\n"
        "📌 *Luật chơi: Nối từ ghép đúng 2 âm tiết tiếng Việt (VD: đua xe ➡️ xe máy ➡️ máy tính)*"
    )
    await ctx.send(msg)

@bot.command(name="bxh", aliases=["top", "xephang"])
async def cmd_leaderboard(ctx):
    """Xem bảng xếp hạng cao thủ nối từ"""
    if not os.path.exists(LEADERBOARD_FILE):
        await ctx.send("📊 Chưa có dữ liệu bảng xếp hạng! Hãy chơi `!pve` hoặc `!nhom` để ghi danh nhé.")
        return
    try:
        with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not data:
        await ctx.send("📊 Bảng xếp hạng đang trống! Hãy làm ván đấu đầu tiên nào.")
        return

    sorted_users = sorted(data.values(), key=lambda x: (x.get("wins", 0), x.get("max_chain", 0)), reverse=True)[:10]

    lines = [
        "🏆 ================================== 🏆",
        "👑 **BẢNG VÀNG CAO THỦ NỐI TỪ SERVER** 👑",
        "🏆 ================================== 🏆"
    ]
    medals = ["🥇", "🥈", "🥉"]
    for idx, entry in enumerate(sorted_users):
        prefix = medals[idx] if idx < 3 else f"`#{idx + 1}`"
        name = entry.get("name", "Người chơi")
        wins = entry.get("wins", 0)
        max_chain = entry.get("max_chain", 0)
        lines.append(f"{prefix} **{name}** — 🏆 **{wins}** trận thắng | Chuỗi dài nhất: **{max_chain}** từ")

    await ctx.send("\n".join(lines))

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
        await message.reply(
            f"❓ Từ **`{word}`** chưa có trong từ điển!\n"
            f"*(Quản trị viên có thể bấm nút bên dưới hoặc gõ `!duyet` để chấp nhận và tiếp tục)*",
            view=view
        )
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

    # Lấy Token từ biến môi trường (an toàn tuyệt đối khi đẩy code lên GitHub)
    if not os.environ.get("DISCORD_TOKEN") and os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("DISCORD_TOKEN="):
                        os.environ["DISCORD_TOKEN"] = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise ValueError("Chưa cấu hình biến môi trường DISCORD_TOKEN!")
    bot.run(token)
