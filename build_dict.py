import csv
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import io
import json
import re
import unicodedata
import urllib.request
from collections import Counter

# ==============================================================================
# 1. CẤU HÌNH CÁC NGUỒN DỮ LIỆU UY TÍN (TRỰC TIẾP TỪ GITHUB & HUGGING FACE)
# ==============================================================================
URLS = {
    # Underthesea NLP: Gộp 3 từ điển (hongocduc + tudientv + wiktionary) ~ 79,226 mục
    "underthesea": "https://raw.githubusercontent.com/undertheseanlp/dictionary/master/dictionary/words.txt",
    # Hugging Face: Từ điển giải nghĩa Tiếng Việt chuẩn (CSV)
    "hf_dict": "https://huggingface.co/datasets/tsdocode/vietnamese-dictionary/resolve/main/vi_dictionary.csv",
    # Viet74K gốc của Duyệt / Hồ Ngọc Đức (Dự phòng bổ sung)
    "viet74k": "https://raw.githubusercontent.com/duyet/vietnamese-wordlist/master/Viet74K.txt",
}

OUTPUT_FILE = "words.txt"

# Chỉ lọc tần suất tối thiểu đối với các từ CHỈ xuất hiện ở 1 nguồn cũ (không có trong Wiktionary/TudienTV)
MIN_FREQ_UNVERIFIED = 2

# Regex kiểm tra âm tiết tiếng Việt hợp lệ (chỉ gồm chữ cái tiếng Việt viết thường)
VIET_CHARS = "a-zàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
VALID_TWO_SYLLABLE_RE = re.compile(rf"^[{VIET_CHARS}]+\s[{VIET_CHARS}]+$")

# Phụ âm đầu không tồn tại trong tiếng Việt chuẩn (lọc từ tiếng Anh/từ lỗi)
INVALID_CONSONANT_RE = re.compile(r"[fjwz]")

# ==============================================================================
# 2. BỘ CHUYỂN ĐỔI ĐỒNG BỘ DẤU THANH (KIỂU CŨ <-> KIỂU MỚI: oà <-> òa, uỷ <-> ủy)
# ==============================================================================
TONE_PAIRS = [
    ("oà", "òa"), ("oá", "óa"), ("oả", "ỏa"), ("oã", "õa"), ("oạ", "ọa"),
    ("oè", "òe"), ("oé", "óe"), ("oẻ", "ỏe"), ("oẽ", "õe"), ("oẹ", "ọe"),
    ("uỳ", "ùy"), ("uý", "úy"), ("uỷ", "ủy"), ("uỹ", "ũy"), ("uỵ", "ụy"),
]

def normalize_text(word: str) -> str:
    """Chuẩn hóa Unicode NFC, chữ thường và khoảng trắng."""
    word = unicodedata.normalize("NFC", word.strip().lower())
    word = re.sub(r"\s+", " ", word)
    return word

def to_modern_tone(word: str) -> str:
    """Đưa toàn bộ từ về chuẩn dấu thanh kiểu mới (òa, óe, ủy) để thống nhất."""
    for old_t, new_t in TONE_PAIRS:
        # Lưu ý: nếu sau nguyên âm còn phụ âm cuối (vd: toàn, hoàn, tuyệt) thì giữ nguyên
        # Chỉ chuyển khi nằm ở cuối âm tiết (trước khoảng trắng hoặc cuối chuỗi)
        word = re.sub(rf"{old_t}(?=\s|$)", new_t, word)
    return word

def generate_tone_variants(word: str) -> set[str]:
    """Sinh cả biến thể dấu cũ và dấu mới để người chơi gõ kiểu nào cũng khớp."""
    variants = {word}
    old_style = word
    new_style = word
    for old_t, new_t in TONE_PAIRS:
        old_style = re.sub(rf"{new_t}(?=\s|$)", old_t, old_style)
        new_style = re.sub(rf"{old_t}(?=\s|$)", new_t, new_style)
    variants.add(old_style)
    variants.add(new_style)
    return variants

def is_valid_2_syllable_word(word: str) -> bool:
    """Kiểm tra từ ghép 2 âm tiết hợp lệ."""
    if not VALID_TWO_SYLLABLE_RE.match(word):
        return False
    if INVALID_CONSONANT_RE.search(word):
        return False
    s1, s2 = word.split(" ")
    # Loại bỏ các âm tiết quá dài bất thường (trên 7 ký tự) hoặc không có nguyên âm
    if len(s1) > 7 or len(s2) > 7:
        return False
    return True

# ==============================================================================
# 3. HÀM TẢI VÀ PHÂN TÍCH TỪNG NGUỒN
# ==============================================================================
def fetch_url(url: str) -> str:
    print(f"⏳ Đang tải: {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")

def build_dictionary():
    # Tập hợp từ "Đáng tin cậy cao" (có trong Wiktionary, TudienTV, HF CSV, hoặc >= 2 nguồn)
    trusted_words = set()
    # Tập hợp từ chỉ xuất hiện ở 1 nguồn cũ (như hongocduc / Viet74K)
    unverified_words = set()

    # --- Nguồn 1: Underthesea NLP Dictionary (JSONL) ---
    try:
        raw_uts = fetch_url(URLS["underthesea"])
        uts_count = 0
        for line in raw_uts.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                w = to_modern_tone(normalize_text(data.get("text", "")))
                sources = data.get("source", [])
                if is_valid_2_syllable_word(w):
                    uts_count += 1
                    # Nếu có trong tudientv hoặc wiktionary, hoặc xuất hiện ở >= 2 nguồn -> Trusted 100%
                    if "tudientv" in sources or "wiktionary" in sources or len(sources) >= 2:
                        trusted_words.add(w)
                    else:
                        unverified_words.add(w)
            except json.JSONDecodeError:
                continue
        print(f"✅ Underthesea NLP: Đã quét {uts_count:,} từ 2 âm tiết.")
    except Exception as e:
        print(f"⚠️ Không thể tải Underthesea: {e}")

    # --- Nguồn 2: Hugging Face Vietnamese Dictionary (CSV có định nghĩa) ---
    try:
        raw_hf = fetch_url(URLS["hf_dict"])
        hf_count = 0
        reader = csv.reader(io.StringIO(raw_hf))
        for row in reader:
            if not row:
                continue
            w = to_modern_tone(normalize_text(row[0]))
            if is_valid_2_syllable_word(w):
                trusted_words.add(w)  # Từ có định nghĩa trong CSV -> Trusted 100%
                hf_count += 1
        print(f"✅ Hugging Face CSV: Đã thêm {hf_count:,} từ chuẩn có định nghĩa.")
    except Exception as e:
        print(f"⚠️ Không thể tải Hugging Face CSV: {e}")

    # --- Nguồn 3: Viet74K (Plain text) ---
    try:
        raw_v74 = fetch_url(URLS["viet74k"])
        v74_count = 0
        for line in raw_v74.splitlines():
            w = to_modern_tone(normalize_text(line))
            if is_valid_2_syllable_word(w):
                v74_count += 1
                if w not in trusted_words:
                    unverified_words.add(w)
        print(f"✅ Viet74K: Đã quét {v74_count:,} từ 2 âm tiết.")
    except Exception as e:
        print(f"⚠️ Không thể tải Viet74K: {e}")

    # --- Nguồn 4: Danh sách từ hiện đại / đời sống bổ sung trực tiếp ---
    modern_custom = [
        "anh hùng", "ảo ảnh", "ẩm thực", "bánh mì", "bánh tráng", "bàn phím",
        "bão bùng", "bập bênh", "bình chọn", "bình luận", "buồm lái", "cà phê",
        "cập nhật", "cấu hình", "chất lượng", "chốt đơn", "chuyển khoản", "cộng đồng",
        "cư dân", "dữ liệu", "đăng ký", "đăng nhập", "đầu tư", "điện thoại",
        "đồ họa", "đồng đội", "đột biến", "đường truyền", "giao diện", "giải trí",
        "giảm giá", "hệ thống", "hiện đại", "hiệu ứng", "hoạt hình", "huýt sáo",
        "kết nối", "khách hàng", "khởi nghiệp", "khuỷu tay", "kiểm duyệt", "kỹ năng",
        "lập trình", "lấp lánh", "lưu trữ", "mạc treo", "mạng lưới", "mật khẩu",
        "máy chủ", "máy tính", "năng lượng", "ngân hàng", "ngoằn ngoèo", "người dùng",
        "nhiệm vụ", "nhột nhạt", "nội dung", "phần cứng", "phần mềm", "phát sóng",
        "phát triển", "phụ kiện", "quản trị", "quét mã", "sáng tạo", "sự kiện",
        "tài khoản", "tải xuống", "tương tác", "thị trường", "thiết bị", "thông minh",
        "thuật toán", "thương mại", "tiện ích", "tối ưu", "trải nghiệm", "trí tuệ",
        "trực tuyến", "tự động", "ứng dụng", "vận hành", "vi xử lý", "xu hướng"
    ] + ["đua xe", "nhượng quyền", "chuyển nhượng", "nhượng bộ", "nhượng lại", "xe máy", "xe đạp", "xe hơi", "xe buýt", "xe tải", "xe khách", "xe cộ", "xe ôm", "xe đua", "quyền lợi", "quyền lực", "quyền hạn", "ủy quyền", "phân quyền", "bản quyền", "chính quyền"]
    for w in modern_custom:
        w_norm = to_modern_tone(normalize_text(w))
        if is_valid_2_syllable_word(w_norm):
            trusted_words.add(w_norm)

    # ==========================================================================
    # 4. LỌC NHIỄU NHẸ VÀ SINH BIẾN THỂ DẤU THANH
    # ==========================================================================
    # Đếm tần suất âm tiết trên tổng tập hợp (trusted + unverified)
    all_candidates = trusted_words | unverified_words
    syllable_freq = Counter()
    for w in all_candidates:
        s1, s2 = w.split(" ")
        syllable_freq[s1] += 1
        syllable_freq[s2] += 1

    final_base_words = set(trusted_words)
    kept_unverified = 0
    for w in unverified_words:
        if w in final_base_words:
            continue
        s1, s2 = w.split(" ")
        # Chỉ giữ từ chưa kiểm chứng nếu cả 2 âm tiết xuất hiện >= MIN_FREQ_UNVERIFIED
        if syllable_freq[s1] >= MIN_FREQ_UNVERIFIED and syllable_freq[s2] >= MIN_FREQ_UNVERIFIED:
            final_base_words.add(w)
            kept_unverified += 1

    print(f"📊 Thống kê trước khi đồng bộ dấu thanh:")
    print(f"   - Từ chuẩn đã xác thực (Wiktionary / TudienTV / HF): {len(trusted_words):,} từ")
    print(f"   - Từ bổ sung từ Hồ Ngọc Đức / Viet74K (đã lọc nhiễu): {kept_unverified:,} từ")
    print(f"   - Tổng số từ gốc 2 âm tiết: {len(final_base_words):,} từ")

    # Sinh thêm biến thể dấu thanh (òa/oà, úy/uý) để người chơi gõ kiểu nào cũng nhận
    expanded_words = set()
    for w in final_base_words:
        expanded_words.update(generate_tone_variants(w))

    sorted_words = sorted(expanded_words)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for w in sorted_words:
            f.write(w + "\n")

    print(f"🎉 Hoàn tất! Đã lưu {len(sorted_words):,} mục từ (kèm biến thể dấu thanh) vào '{OUTPUT_FILE}'.")

if __name__ == "__main__":
    build_dictionary()