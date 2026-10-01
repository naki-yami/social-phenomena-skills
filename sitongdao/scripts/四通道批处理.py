# -*- coding: utf-8 -*-
"""
四通道批处理 v1.0
把《社会现象分析体系》的四条检验通道 + 失效模式 #20 / #23 / #27 做成可执行的脚本。

用法：
  python 四通道批处理.py <路径...>  [--out 报告.md] [--json out.json]
                                   [--terms 词表.txt] [--kw 词1 词2 ...]
                                   [--channels 1234] [--quiet]

设计原则（全部来自体系）：
  · 先锁字形，正如先锁版本（#20）——关键词自动做繁／简双查
  · 报数前先跑邻字检查（#27）——子串假阳性自动暴露
  · 专名／通名双查（#27）——通名假阴性自动暴露
  · 判错通道 = 判出假结论——四条通道分别打分，不合并成一个总分

字典：同目录 简繁字典.tsv（提取自 OpenCC，Apache-2.0）
"""
import io, os, re, sys, json, argparse, collections

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 字典
def load_dict(path=None):
    path = path or os.path.join(HERE, "简繁字典.tsv")
    S2T, T2S, PS2T, PT2S = {}, {}, {}, {}
    cur = None
    tgt = {"@CHAR_S2T": S2T, "@CHAR_T2S": T2S, "@PHRASE_S2T": PS2T, "@PHRASE_T2S": PT2S}
    if not os.path.exists(path):
        return S2T, T2S, PS2T, PT2S
    for line in io.open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            cur = tgt.get(line.strip())
            continue
        if cur is None:
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] and parts[1]:
            cur[parts[0]] = parts[1]
    return S2T, T2S, PS2T, PT2S

S2T, T2S, PS2T, PT2S = load_dict()
# 字形集合的取法（#20 的命门）：必须取转换表的**键**，不能取**值**。
# 取值的坑：T2S 里 唸->念、齣->出、慾->欲、韆->千…… 会把「念/出/欲/千/佛/向」
# 这类繁简共用的中性字误判成「简专用」，在一部繁体经文里刷出上万条假阳性。
TRAD_ONLY = {k for k, v in T2S.items() if v != k}
SIMP_ONLY = {k for k, v in S2T.items() if v != k} - TRAD_ONLY

def to_trad(s):
    """简 -> 繁：先做词组匹配（长优先），再做字级。"""
    out, i, n = [], 0, len(s)
    keys = None
    while i < n:
        hit = None
        for L in (6, 4, 3, 2):
            if i + L <= n:
                seg = s[i:i+L]
                if seg in PS2T:
                    hit = PS2T[seg]; i += L; break
        if hit:
            out.append(hit); continue
        c = s[i]
        out.append(S2T.get(c, c)); i += 1
    return "".join(out)

def to_simp(s):
    out, i, n = [], 0, len(s)
    while i < n:
        hit = None
        for L in (6, 4, 3, 2):
            if i + L <= n:
                seg = s[i:i+L]
                if seg in PT2S:
                    hit = PT2S[seg]; i += L; break
        if hit:
            out.append(hit); continue
        c = s[i]
        out.append(T2S.get(c, c)); i += 1
    return "".join(out)

def script_of(text, lang=None):
    """返回 (判定, 繁占比, 繁专用字数, 简专用字数, 异常字表)

    V37 · A17（#52）：**先判语种，再判字形**。日文汉字是旧字体（閣／時／強），
    拿繁简字典双查会把日本官文误报成「繁简混用 0.717」——本工具不做日文旧字体的繁简判定。"""
    if lang in ("ja", "ko"):
        return ("日文汉字（旧字体），不做繁简双查" if lang == "ja" else "韩文汉字（不判定）", 0.0, 0, 0, [])
    t = sum(1 for c in text if c in TRAD_ONLY)
    s = sum(1 for c in text if c in SIMP_ONLY)
    tot = t + s
    if tot == 0:
        return "无字形特征", 0.0, t, s, []
    r = t / tot
    verdict = "繁體" if r >= 0.90 else ("简体" if r <= 0.10 else "混合")
    bad = collections.Counter(c for c in text if (c in TRAD_ONLY) != (verdict == "繁體") and (c in TRAD_ONLY or c in SIMP_ONLY))
    return verdict, r, t, s, bad.most_common(8)

# ---------------------------------------------------------------- 载入
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
def han_count(t): return len(HAN.findall(t))
def nk(t): return max(1, han_count(t))

def read_text(p):
    raw = io.open(p, "rb").read()
    enc = "utf-8"
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    else:
        for e in ("utf-8", "gb18030", "big5"):
            try:
                raw.decode(e); enc = e; break
            except Exception:
                continue
    return raw.decode(enc, "replace"), enc

def collect(paths, exts=(".md", ".txt", ".markdown")):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, fs in os.walk(p):
                for f in sorted(fs):
                    if f.lower().endswith(exts):
                        files.append(os.path.join(root, f))
        elif os.path.isfile(p):
            files.append(p)
    return files

# 前置块有两种形制：YAML（--- 包裹，key: value）与 TOML（+++ 包裹，key = "value"）。
# 只在前置块里找成文年，避免正文里的「date = ...」误命中。
FM_BLOCK = re.compile(r"\A\s*(?:---[ \t]*\n.*?\n---[ \t]*|\+\+\+[ \t]*\n.*?\n\+\+\+[ \t]*)", re.S)
FM_YEAR  = re.compile(r"^\s*(?:created|updated|date|发布时间|时间|日期)\s*[:=]\s*[\"\']?(\d{4})", re.M)
def fm_year(t):
    """#29 的**镜像用法**：frontmatter 不能冒充内容，但可以**定位成文年**。
    E3 实测（V31）：缠论原文成文 2006-2008，用默认基准年 2026 判 ① ——
    未来时点 **96 → 0**（568 处全被吞成「历史引用」）。**基准年写死 = 给历史文本判死刑。**"""
    m = FM_BLOCK.match(t)
    blk = m.group(0) if m else t[:2000]
    ys = [int(x) for x in FM_YEAR.findall(blk)]
    return min(ys) if ys else None

def strip_frontmatter(t):
    if t.startswith("---"):
        i = t.find("\n---", 3)
        if i > 0:
            j = t.find("\n", i + 4)
            return t[j+1:] if j > 0 else t
    return t

# ---------------------------------------------------------------- 四通道
C1_FUTURE = [r"當來", r"未來世?", r"末世", r"末法", r"當有", r"將(?:來|要|會)", r"預測", r"預計"]
C1_DATE   = [r"\d{4}\s*年", r"[〇零一二三四五六七八九十百]{2,4}年", r"\d{1,2}\s*月\s*\d{1,2}\s*日", r"Q[1-4]", r"\d{4}[-/]\d{1,2}"]
# V24 ① 重定义：不是「形如日期的东西」，是「**本文可结算的时点**」＝未来指向 ＋ 与该文主体绑定。
#   既往失误：货币战争读书笔记 12/千字全是 19xx 历史年份；第17章 的 3 处全是 frontmatter ＋ 举例。
#   绑定用「同句共现」近似：未来年份／日期必须与未来指向词出现在同一句，才计为本文的时点。
C1_NOW_YEAR = 2026        # 语料基准年：全库成文时间集中在 2026（345/408 份有 frontmatter），原文类无日期者同此基准
C1_NOW_MD   = (9, 30)      # 语料基准日（本库最新流水账 2026-09-28；会话基准 2026-09-30）
C1_FUTWORD = r"(?:將|将会|將於|預計|预计|預測|预测|展望|有望|目標價|目标价|大概率|或將|届时|屆時|稍後|此後|計劃|计划|预期|預期)"
# V33 · A2：**量词型相对时点**——「数周内／几天之内」是**时长**，不是时点
#   （A-1 实测：AIHOT「命令将在数周内发出」= 0）。**但裸补会翻车**：全库 A/B 净增 fut 7 处，
#   逐条核验 **7/7 假阳性**（货币战争4/5：「法郎在几天之内…反弹」「战后几年内修建」
#   「美国在短短的几年之内…沦落」——全是**回顾式**叙事）。
#   ⇒ 装法：**必须与前瞻标记同现**（将／即／预计／计划／拟／有望／打算／准备 ≤4 字内）。7 → 0，AIHOT 那处保住。
C1_RELTIME = r"(?:今後|今后)(?:的)?[一二三四五六七八九十百\d]{1,4}(?:年|个月|個月|週|周)|明年|下(?:個|个)?(?:月|季度|週|周)|本季度|下季度|三季度|四季度|年底前|今(?:年內|年内|年底)|(?:未來|未来)[一二三四五六七八九十百\d]{1,4}(?:年|个月|個月|週|周)|[一二三四五六七八九十\d]{1,3}(?:個月|个月|週|周|交易日)(?:內|内)|窗口期|(?:兩會|议息|議息|FOMC|大會|大会)(?:後|后)|(?:將|将|即將|即将|預計|预计|預期|预期|計劃|计划|擬|拟|有望|打算|準備|准备)[^，。；]{0,4}(?:數|数|幾|几)\s*(?:日|天|週|周|个月|個月|年)(?:內|内|之内|之內|以内|以內)"
C1_YEAR    = r"(\d{4})\s*年"
C1_FUTDATE = r"(?<![0-9A-Za-z])20\d{2}\s*[-/]\s*\d{1,2}(?:\s*[-/]\s*\d{1,2})?(?![0-9A-Za-z])(?!\s*年)"
C1_ABSDATE = r"(?:\d{4}\s*年|\d{4}[-/]\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*日)"
# V32 ① 体裁闸门（A-1 实测）：日程语「将于＋具体时刻」在**机构性文本**里是可结算时点，
#   在**叙事体**里只是叙述时间 ⇒ 必须过体裁闸门，否则重犯 #24 同形异域。
#   实测依据：全库 `将于` 6 处、`今年N月` 33 处，命中 11 份，**全部在 10_原始资料 叙事体**（救世主 21／天幕红尘 3…）。
C1_SCHED      = r"(?:將於|将于|將在|将在|定於|定于|擬於|拟于|擇期|择期)"
C1_SCHED_TIME = r"(?:(?:今年|本年|次年|翌年|同年)[一二三四五六七八九十\d]{1,2}月|(?<!年)[一二三四五六七八九十\d]{1,2}月[一二三四五六七八九十\d]{1,2}日)"
C1_INST_WORDS = ("外交部", "国务院", "白宫", "发言人称", "公报", "通稿", "新闻办", "新华社", "新闻发布会", "公告", "声明", "记者")
C1_DATELINE = re.compile(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?\s*$", re.M)
def is_institutional(t):
    """机构性文本判据。**V32 标定记录（A-1，两次都试过）**：
      ① 首版 = 时间戳 ∨ 机构词≥2 —— **标定失败**：全库放行 19 份（含《遥远的救世主》《货币战争》全五部、
         巫师财经），净增 2 处未来时点、人工核验**精确率 0/2**（「物价将在9月1日放开」是 1988 回忆录叙述、
         「白银市场将在…2月5日被袭击」是 2010 历史引述）⇒ **不装**（未过 80% 门槛）。
      ② 现版 = **文首 300 字内的独立通稿日期行**（标题下的 YYYY-MM-DD[ HH:MM]，官方通稿的硬形制）。
         实测：库内 393 份**放行 0 份**（知识库不含通稿）⇒ 全库读数零改动；新闻语料 7/7 正确放行。
      ⇒ 这是一条**只对机构通稿生效、对库内语料完全惰性**的窄闸门。"""
    return bool(C1_DATELINE.search(t[:300]))
# V33 ① **刊头闸门**（A1 · #38「版面日期冒充本文时点」）：刊头行（刊名／期号／发刊日期行／往期导航）
#   是**版式信息**，不是本文的判断；文首的标题行（H1–H6）同属版式（#29 的 H1 缺口）。
#   实测依据：AIHOT 10/1 日报判「可结算・未来时点 5 处」，逐条打出来 **5/5 全在刊头**；
#   头条那 1 处来自**语料自带的 H1 标题**。⇒ 只排除刊头行**自身**，其后的正文一个不动。
#   行**长度**是第一道闸：正文里的日期句（「2026年9月30日，METR 主席…」）是**长句**，不会被误伤。
#   第二道闸 = **整行是句子**（含。！？）的不算版式行。全库实测：不加这道闸，误伤 ≈10 行
#   （「2026年1月20日夜，莫斯科。」「甲的生辰：…时」「改法：…链接打通。（同类问题 …」）⇒ 加后只剩 ≈4 行。
C1_MARST = re.compile(
    r"^\s*[#>*\-\u2022]*\s*(?:"
    r"第\s*[0-9\u4e00-\u9fff]{1,4}\s*期[^\n]{0,50}"
    r"|[^\n]{0,20}(?:日\s*报|周\s*报|月\s*报|晚\s*报|早\s*报|简\s*报|快\s*报|周\s*刊|期\s*刊|合\s*订\s*本)[^\n]{0,44}"
    r"|[^\n。！？]{0,20}\d{4}\s*年\s*\d{1,2}\s*月[^\n。！？]{0,44}"
    r"|[^\n。！？]{0,20}\d{4}[-/]\d{1,2}[-/]\d{1,2}[^\n。！？]{0,44}"
    r")\s*$", re.M)
C1_MARST_LINES = 40
def masthead_spans(t):
    """刊头行的字符区间（只在前 C1_MARST_LINES 行内认）。含**文首第一个标题行**。"""
    spans, off, first = [], 0, True
    for ln in t.split("\n")[:C1_MARST_LINES]:
        end = off + len(ln)
        if first and ln.strip():
            first = False
            if re.match(r"^\s*#{1,6}\s", ln):
                spans.append((off, end + 1)); off = end + 1; continue
        if C1_MARST.match(ln):
            spans.append((off, end + 1))
        off = end + 1
    return spans
C1_HISYEAR = r"(?:1[0-9]{3}|20[0-2][0-5])\s*年"
# 引述自查：落在 引号／反引号 内的未来时点 = 他人承诺（政策文件、快讯、公告），不是本文的判断
C1_QSPAN   = r"[\u300c\u300e\u201c\x22][^\u300d\u300f\u201d\x22]{1,600}?[\u300d\u300f\u201d\x22]|`[^`\n]{1,600}?`"
# 引述框架（V25・无引号的转述）：与引号同等对待，带来的未来时点也归他人承诺。
#   装的三类（等距抽检精确率高）：报道前缀、《书名》＋言说动词、文件＋言说动词、签署类协议
#   不装的（实测否决）：「据…了解／介绍」（小说对白引述，且 `根据` 吞并 `据`）、
#   「发布」（命中全是 `发布时间：` 元数据 = #29）、「宣布／承诺／披露」（601 处，多为已发生动作）、
#   「预计／计划／拟／有望／目标」（1351 处，命中全是「计划经济」「计划调拨」「加载目标」）。
#   V31 装「数据显示／统计显示／表明」（A′）：库外 216 篇 A/B 实测**未来时点 258→253、引述内 24→29**
#     ——那是**第三方的数**，不是本文的判断。（同轮试的「有分析称／消息人士称／业内人士」效应为 0，暂不装。）
C1_ATTR = (r"(?<!根)据[^，。；、！？]{0,12}(?:报道|悉|称|透露|消息)"
           r"|(?:报道|消息|快讯)(?:称|显示)"
           r"|《[^》]{1,40}》(?:指出|显示|称|提出|明确|预计|展望|披露)"
           r"|(?:數據|数据|統計|统计)(?:顯示|显示|表明)"
           r"|(?:公告|通知|文件|规划|报告|白皮书|声明|通报|纪要|财报)(?:称|显示|指出|提出|明确|预计|披露)"
           r"|(?:签署|签订|达成)[^，。；]{0,14}(?:协议|合同|备忘录|条约|协定)"
           # V33 · A4：扩族到「文件／报道＋写明／载明／披露」与「…披露：」。
           #   A-1 实测：s3（IT之家转路透）三处引述内承诺全落 own——「路透社查阅的文件显示」
           #   「该报道同时披露」「SpaceX 同时披露：」这三类不在表内。判据仍是**无引号的转述**，与 V25 同权。
           r"|(?:報道|报道|消息|快訊|快讯)[^，。；]{0,4}(?:披露|顯示|显示|寫明|写明|載明|载明|稱|称|指出|表示)"
           r"|(?:文件|公告|通知|規劃|规划|報告|报告|白皮書|白皮书|聲明|声明|通報|通报|紀要|纪要|財報|财报|申報文件|申报文件|招股書|招股书|S-?1|材料)[^。；]{0,6}(?:寫明|写明|載明|载明|顯示|显示|披露|指出|稱|称|提到|表示)"
           r"|[^，。；]{0,10}(?:同時|同时)?披露[:：]")
# 当下锚定：文中出现 >= 基准年-2 的绝对年份，才说明文本锚在「现在」
C1_YEAR_MD   = re.compile(r"\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?")
# V37 · A13（#50 编号冒充日期）：`YYYY-MM` 的月份必须合法（1–12），且不得是**编号上下文**的一部分——
#   前邻是字母数字（`XJHW2026-127` 的 `W`）即编号；后邻是数字或 `-`（`…2026-198` 的 `8`）即编号继续。
#   P5 实测：10 份废标公告里 3 处编号被读成日期（`XJHW2026-127`→`2026-12`、`XJCC-ZB-2026-198`→`2026-19`、
#   `青政采询价（货物）2026-283-1号`→`2026-28`），修后 3 处全消失；负数对照 `2027-03` 这类真日期保持命中。
C1_YEAR_DASH = re.compile(r"(?<![0-9A-Za-z])(\d{4})\s*[-/]\s*(0?[1-9]|1[0-2])(?:\s*[-/]\s*(\d{1,2}))?(?![0-9\-])")
# V37 · A13 配套：**年份区间**（`（1956-1959）`／`1949-1984年`）不是「年-月」。
#   月份合法性装上后，旧的 `1956-19` 误读消失（月 19 非法）——但区间本身仍是**时点**：
#   `《毛泽东年谱》（1956-1959）` 若一并丢掉就是真损失。故补一条区间规则：取**较早**的一年；
#   区间尾年若已带「年」字，年份规则已计过（`used` 重叠检查），不重复。
C1_YEAR_RANGE = re.compile(
    r"(?<![0-9A-Za-z])((?:1[0-9]{3}|20[0-9]{2}))\s*[-\u2013\u2014/]\s*((?:1[0-9]{3}|20[0-9]{2}))(?![0-9])")
#   缩写区间（`1937-38`／`1910-13`，尾年只写两位）**要与 `YYYY-MM` 日期分开**：
#   据「尾两位数 ≥ 年尾两位数」判——`1937-38`(38≥37 ✓)／`1910-13`(13≥10 ✓) 是区间；
#   `2026-12`(12<26 ✗) 是日期，交回 C1_YEAR_DASH。
C1_YEAR_RANGE2 = re.compile(
    r"(?<![0-9A-Za-z])((?:1[0-9]{3}|20[0-9]{2}))\s*[-\u2013\u2014/]\s*(\d{2})(?![0-9])")
# V33 · A3：**无年份月日**。A-1 实测漏检：「截止 10 月 2 日上午 8 点」= 0。
#   闸门：① 须有「截至／截止／至迟／直到」这类**期限标记**（裸月日太脏）；
#         ② 须有**当下锚定**；③ 解出的（月,日）必须**晚于基准日**——否则是已过日期。
C1_MD_DEAD = re.compile(r"(?:截至|截止|至遲|至迟|直到)\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?")
# （未来日期由 C1_YEAR_DASH 处理，见 fut_points）
C1_ACTION = [r"買|賣|加倉|減倉|買入|賣出|下注|建倉|清倉", r"應(?:當|該)(?:買|賣|做|採取)", r"目標價", r"止損"]
C2_QUOTE  = [r"[「『][^」』]{2,60}[」』]", r"[“”][^“”]{2,60}[“”]"]   # V33 · A5：删《…》——书名号≠引号（机构名≠引文）
C2_CITE   = [r"見[《「]", r"引自", r"轉引", r"參見", r"\([^)]*(?:vol|p\.|頁|卷)[^)]*\)", r"第[一二三四五六七八九十百]+卷", r"注\s*\d+",
             # V33 · A6：新闻体引注形制。学术引注之外，新闻体的「据《X》报道」「（注：…）」「原文」行同样是外证形制。
             #   A-1 实测：s2（IT之家转《纽约时报》）有 9 处具名信源原话＋「据《纽约时报》报道」，却因字段只认学术形制而判弱。
             r"《[^》]{1,40}》(?:報道|报道)", r"[據据][^，。；、！？\s]{0,10}(?:報道|报道)",
             # 不装「（注：」：全库 4 处，抽检**全部是作者夹注**（「（注：当时拿破仑三世尚未称帝）」），
             #   IT之家那类「（注：现汇率约合…）」也是释义而非来源 ⇒ 精确率 0/4，不装。
             r"^\s*原文\s*$", r"^\s*來源\s*[:：]", r"^\s*来源\s*[:：]"]
# V22 标定后定稿（文言表同样抽检，见 库内地基_通道实测.md 附节）
#   删 然而(0%)／但是(0%)：文言里是「寂然而住」「但(=只)+是」，在文言语料上 100% 假阳性
#   删 謂…故(50%)：正则跨度太宽，不锚定语义；删 所謂…者(50%)：佛典中多为列举而非定义
#   收窄 云何(50%)：问定义(云何名/為/是/謂) vs 问方式(=怎么)，同形异义
#   收窄 何等(65%)：何等為/是何 才是设问定义，裸「何等」常作「何种／任何」
# V23 拆成两套文言子表（第三套表＝译经体）。抽检见 库内地基_通道实测.md 附三
#   论书体（玄奘／注疏）：者，謂 100%、所言…者 80%、云何名/為/是/謂 收窄后可用
#   译经体（汉译佛典）：何等為/是 100%、是名 100%、名為 90%、所謂 60%->收窄(须前接定义框架)
#   未装：即非|則非 55%（它是「遮诠」论式标记，不是定义标记）
C3_DEF_LUN  = [r"者，謂", r"所言[^，。；]{1,12}者", r"云何(?:名|為|是|謂)"]
C3_DEF_JING = [r"何等(?:為|是)", r"是名", r"名為",
               r"(?:者|故名|名為|是名)[^。，]{0,8}[，、]?所謂"]
C3_DEF    = C3_DEF_LUN + C3_DEF_JING
C3_ARG    = [r"何以故", r"是故", r"若[^。]{1,20}?則", r"以[^。]{1,10}故", r"所以者何",
              r"由此(?:故|應說|可知|當知)"]
# ③ 的两套表（#28 解药）：判据必须与文本的**语言年代**对齐。
# 原表 = 文言表；下表 = 白话表。实测分度（白话/经文，每千字）：
#   就是 2.34/0.16（15×）、因为 1.97/0.06（33×）、其实 0.88/0.05（18×）、
#   而且 0.25/0.02（12×）、所以 1.28/0.26（5×）  ← 白话端
#   何以故 0.00/0.33（33×）、是故 0.03/0.63（21×）            ← 文言端
# V21 标定后定稿（抽检见 库内地基_通道实测.md 附：每个标记的精确率）
#   删除：这说明(1/1 是反例)、由此可见(全库 0 命中)、就是下调(见下)
#   修正 所以->所以(?!立)：文言短语「患，所以立」被白话连词吞掉（全库 19 次）
#   修正 叫做->叫做(?=[「『“])：称呼/指称（被学生叫做哲学王子）不是定义，只有术语命名才是
C3_DEF_BAI = [r"意思是", r"指的是", r"也就是说", r"换句话说", r"定义为",
              r"称之为", r"叫做(?=[「『“])", r"就是"]
# 论证连词不再混算一个数（#21 类型误判的同构）：因果／纠偏／递进是三种不同的推理动作
C3_ARG_BAI   = [r"因为", r"所以(?!立)", r"因此", r"既然"]        # 因果类
C3_ARG_BAI_R = [r"其实", r"反而", r"恰恰相反", r"恰恰是"]        # 纠偏类
C3_ARG_BAI_A = [r"而且", r"不仅如此", r"更重要的是"]            # 递进类
# #34 同形异能（语用层）：④ 要找的是「**对读者的禁令**」，不是「禁止」本身。四种同形全不是：
#   ① 遮诠（不可说／不可言）——说的是「**对象**离言」，不是「你」不许问（华严经「不可说」×1311 是**数量词**）；
#   ② 叙述省略（不说／略而不说）——说的是「**本论**没讲这一项」（述记 381 处）；
#   ③ 论辩承许（不許／不许）——说的是「**对方**不承认某命题」（廣百論「汝不許為依他起性」241 处）；
#   ④ 论式评断／戒条（不應／不应）——「不應道理」是**评断**、「皆不應行」是**戒律**（教义侧 3447 处）。
#   ⇒ **一个词只做一次言语行为判断**：不是禁令就不进 ④。逐条实测见 库内地基_通道实测.md 附十一。
# #34 的另一半：**词形太窄 = 漏检**。旧表只有 `誹謗|诽谤`（双字复合），漏掉 `谤毀／謗毀／謗佛／謗諸佛`——
#   这正是《遥远的救世主》第二十章「谤佛之嫌」没被算进去的原因（当时记为「漏检恰好正确」）。
#   改 `[誹诽]?[謗谤]`（并删冗余的 `謗法|谤法`，已被覆盖）。两侧 A/B：教义侧 +485 ／ 白话侧 +11（每份 <3，够不着阈值）
#   ⇒ **④ 命中集合一份不变**（库内仍 12 份），只有读数变准（瑜伽師地論 121→201、大智度論 237→284）。
C4_FORBID = [r"[誹诽]?[謗谤]", r"不生信", r"不信", r"罪報|罪报", r"墮地獄|堕地狱", r"但應仰信|但应仰信", r"不應誹謗|不应诽谤", r"勿生疑", r"不應生疑"]
# #33 同形异域：`不信` 是**同形词**——译经体里「不信（受）」＝教义条款，
#   白话体里「年轻人不信未来」＝世俗态度。全库实测（见 库内地基_通道实测.md 附十 · 第 2 节）：
#     宽表 `不生信|不信` = 164 处；收窄到「不信＋佛法僧／因果业经／受解／大乘正法／罪报」= 16 处。
#   若**全局收窄**，经文侧真阳性被大量打掉：大乘起信论直解 11→0（全是「毁谤不信」「生于不信」）、
#     华严经 10→3（「不信调御」「有生疑不信者」「不信不知菩萨功德」）、法华经 17→5、少室六门 8→1。
#   ⇒ **不装全局收窄**（同 V25 记录过的「实测后不装」），改为**体裁闸门**：
#     译经体／论书体（教义性文本）用宽表；白话体（世俗论说）用**强核＋教义宾语**。
C4_FORBID_S = [r"[誹诽]?[謗谤]", r"不生信", r"罪報|罪报", r"墮地獄|堕地狱",
               r"但應仰信|但应仰信", r"不應誹謗|不应诽谤", r"勿生疑", r"不應生疑"]
C4_FORBID_N = [r"不信(?:佛|法|僧|因|果|業|业|經|经|受|解|大乘|正法|罪|報|报|罪福|善惡|善恶)"]
C4_FORBID_BAI = C4_FORBID_S + C4_FORBID_N
C4_GRADE  = [r"墮地獄|堕地狱", r"罪報|罪报", r"無量劫受苦|无量劫受苦", r"當知是人|当知是人"]
# ④ 的话题切换表（#32 体裁边界）：把「人物回避」从「文本教义性禁止检验」里分出来。
#   区分靠**引号内占比**：小说对白里的「别问了／不说了」是人物回避；
#   经论里叙述层的「不应诽谤／勿生疑」是文本自带的条款。前者引号内占比高，后者低。
#   实测（V28 定稿，逐条人工判；语料＝库内两部长篇 ＋ 库外空有二宗 36 部）：
#     遥远的救世主  forbid 7（引号内 71%）／talk 30（引号内 30，100%） ⇒ 对白；④ 降级
#     天幕红尘      forbid 10（引号内 90%）／talk 20（引号内 17，85%）  ⇒ 对白；④ 降级
#     货币战争1     forbid 2／talk 5（引号内 0）      ⇒ 「只字不提／避而不谈」的叙述层，不降级
#     空有二宗 36 部 forbid 659（合订本）／talk 0     ⇒ 纯教义，无体裁混杂（16 处「別問」是「總問/別問」的义，已剔）
#   人工抽检：两部长篇 talk 50 处里 **42 处是真「回避」（84%）**，8 处是条件句/否定句/成语。
#   故 talk 只作**提示**进 4_talk／4_talk_q，**不并入** 4_forbid。
#   标定收窄（逐条人工判，见 库内地基_通道实测.md 附九）：三种吞并各有实测——
#     ① `别提` 被「特-别提款权」（SDR）吞并 ⇒ **整条删**（货币战争1 上 talk 全是它）；
#     ② `别问` 被「性-别问题」吞并、`不谈` 被「谈-不谈」吞并、`不提` 被「不提出/提倡/提供/任何」吞并 ⇒ 加邻字排除；
#     ③ `算了` 被「盘算了一下／计算了一下／就算了／给律师算了」吞并 ⇒ 改为**要求前置边界**（引号/标点/行首）；
#     ④ `不说了` 被「怎么不说了」（催人继续说，语义相反）吞并 ⇒ 负向后顾 `(?<!么)`。
#   V31 全库等距抽检 20 条（110 处）：假阳性 4（可以不谈／不得不提前／无话不谈／清高到可以不谈）⇒ 80%。
#   不**扩表**（新试 14 个候选：忌讳 8／不便说 1／不必说 5／绝口不提 4／不用说 30(全是让步连词)…，
#   库外一律 0，均够不着阈值），改为**收紧**：不提 ＋排除「前／防／取」（提前／提防／提取）、
#   不谈 ＋排除前邻「以／不／到／话」（可以不谈／不得不谈／高雅到不谈／无话不谈）⇒ 110 → 102 处。
C4_TALK   = [r"(?<!么)不說了|(?<!么)不说了",
             r"不提(?![出及到倡供任高醒前防提取])",
             r"(?<![談以不到话話])不談|(?<![谈以不到话話])不谈",
             r"(?<![性個區特])別問(?=[了吧我他她它這那])|(?<![性个区特])别问(?=[了吧我他她它这那])",
             r"(?:^|(?<=[“”\"「」『』：:，,。！？、\n]))算了",
             r"過去的事|过去的事", r"不說這個|不说这个"]
GEN_HEAD  = [r"^\s*(?:model|reasoning effort|temperature|system|user|assistant|role)\s*[:：]", r"^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", r"^#{2,}\s*(?:Round|Turn|第\s*\d+\s*轮)"]

# ======================= V32 · 语言路由与英文平行判据表 =======================
# 起因（A-2 实测）：美方官方通稿为纯英文，汉字数 = 0，被「不足 50 汉字」**静默跳过** ⇒ 假阴性（与 #26 同族）。
# 纪律：**平行但不合并**——英文表是中文表的「同功能异语言」版本，密度分母 = **每千词**；
#       结果单独成表，**不得与中文读数合并成分数**（否则就是 #9 总分癖的翻版）。
WORD = re.compile(r"[A-Za-z][A-Za-z\'\-]*")
def word_count(t): return len(WORD.findall(t))
def nw(t): return max(1, word_count(t))
# ======================= V37 · A17（#52 第三语种的三条落法） =======================
# 起因（P6 实测）：语言路由只有中／英两档，第三语种**三条落法全错**——
#   ① 西里尔：WORD 只认拉丁、han_count 只认汉字 ⇒ 单位数 26／27 < 50 ⇒ 整份跳过，
#      而**理由写成「正文不足 50 单位」**（两份实际 15,734／16,888 字节，手工探针 51 处／76 处）；
#   ② 拉丁但非英语（德）：判成 en ⇒ **拿英文表量德语**，德语将来式（wird／werden／soll／künftig）一条都不在表内；
#   ③ 汉字圈非中文（日）：汉字占比 0.9997 ⇒ 判成 zh ⇒ **中文表跑在日本官文上**，
#      且字形锁把日本旧字体（閣／時／強）误报成「繁简混用 0.717」。
# 修法：**先按「字符落在哪张表里」判语种，再决定要不要出读数**。只有 zh／en 有判据表；
#   其余（ja／ko／cyr／other）**显式拒绝**——既不落进 en，也不套中文表；跳过时**打印实测单位数**。
KANA   = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9d]")
HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")
CYR    = re.compile(r"[\u0400-\u04ff\u0500-\u052f\u2de0-\u2dff\ua640-\ua69f]")
GREEK  = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
ARAB   = re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff]")
_LET = "A-Za-z\u00c0-\u024f\u0400-\u04ff\u0500-\u052f\u0370-\u03ff\u1f00-\u1fff\u0600-\u06ff\u0750-\u077f"
LETWORD = re.compile("[" + _LET + "][" + _LET + "'’-]*")
def kana_count(t): return len(KANA.findall(t))
def hangul_count(t): return len(HANGUL.findall(t))
def cyr_count(t): return len(CYR.findall(t))
def other_letter_count(t): return len(GREEK.findall(t)) + len(ARAB.findall(t))
def letword_count(t): return len(LETWORD.findall(t))
def lang_of(t):
    """V37：返回 zh ／ en ／ ja ／ ko ／ cyr ／ other。**只有 zh 与 en 有判据表**，其余由调用方显式拒绝。"""
    cjk, wd = han_count(t), word_count(t)          # 保持 V36 口径（zh／en 的分流读数不变）
    kana, hang = kana_count(t), hangul_count(t)
    cyr, oth = cyr_count(t), other_letter_count(t)
    if kana >= 30 and kana >= 0.10 * (cjk + kana): return "ja"
    if hang >= 30 and hang >= 0.10 * (cjk + hang): return "ko"
    if cjk + wd == 0:
        if cyr: return "cyr"
        return "other"
    if cjk / float(cjk + wd) >= 0.35: return "zh"
    if cyr > wd: return "cyr"
    if oth > wd: return "other"
    return "en"
SUPPORTED_LANGS = ("zh", "en")
LANG_NAME = {"zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文", "cyr": "西里尔", "other": "其他"}
def lang_unit(t, lang):
    """报「实测单位数」用：中文＝汉字；英文＝词；其余＝字母词＋汉字。"""
    if lang == "zh": return han_count(t)
    if lang == "en": return word_count(t)
    return letword_count(t) + han_count(t)

E1_FUTWORD = (r"\b(?:will|shall|is (?:expected|slated|set|due) to|are (?:expected|slated|set|due) to"
              r"|intends? to|plans? to|aims? to|expects? to|is to|are to|going to|upcoming"
              r"|in the coming|in the months ahead)\b")
E1_RELTIME = (r"\b(?:(?:by|in|until|through|before|after|over) (?:the end of )?(?:this|next|that|the) "
              r"(?:year|month|week|quarter|summer|fall|autumn|winter|spring)"
              r"|next (?:year|month|week|quarter)"
              r"|within (?:(?:a few|several|two|three|four|five|\d+) )?(?:days|weeks|months|years)"
              r"|in the (?:next|coming) (?:few|several|\d+) (?:days|weeks|months)"
              r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)"
              r"\s+\d{1,2}(?:,\s*\d{4})?)\b")
E1_FUTYEAR = re.compile(r"\b(20\d{2})\b(?!\s*年)")   # V33 · A7：裸年份不吞中文（“到 2029 年”不算英文时点）
E1_QSPAN   = C1_QSPAN + r"|\x22[^\x22\n]{2,400}\x22"
E2_QUOTE   = [r"[\u201c][^\u201d]{2,400}[\u201d]", r"[\u2018][^\u2019]{2,200}[\u2019]", r"\x22[^\x22]{2,400}\x22"]
E2_CITE    = [r"\baccording to\b", r"\bper (?:the )?(?:White House|State Department|U\.S\.)\b", r"\bibid\b",
              r"\([^)]*\b(?:p\.|pp\.|vol\.|no\.|sec\.)\s*\d+[^)]*\)"]
E3_DEF     = [r"\bmeans (?:that|to)\b", r"\brefers to\b", r"\bis defined as\b", r"\bin other words\b",
              r"\bthat is to say\b", r"\bwhich is to say\b"]
E3_ARG     = [r"\btherefore\b", r"\bthus\b", r"\bhence\b", r"\bconsequently\b", r"\bas a result\b",
              r"\bwhich means\b", r"\bbecause\b", r"\bin order to\b"]
E4_FORBID  = [r"\bmust not be questioned\b", r"\bdo not question\b",
              r"\bcannot be (?:verified|questioned|doubted)\b", r"\bbeyond (?:human )?(?:understanding|comprehension)\b",
              r"\bonly time will tell\b", r"\btake it on faith\b", r"\bdo not (?:test|doubt)\b",
              r"\bmust (?:simply )?believe\b"]
E4_GRADE   = [r"\bmust not be questioned\b", r"\bcannot be (?:verified|questioned|doubted)\b", r"\bdo not (?:test|doubt)\b"]

def dens_en(t, pats):
    return sum(len(re.findall(p, t, re.I)) for p in pats) * 1000.0 / nw(t)
def rawc_en(t, pats):
    return sum(len(re.findall(p, t, re.I)) for p in pats)

def _fut_in_sent_en(s, off, by, bm, bd, anchored, qs, cpos=None):
    """英文版 `_fut_in_sent`——① 文本层与 #41／#51 取消层的**公共内核**（V37 起英文侧也有取消层）。
    V37 · A15（#48①）：落在行内引注里的年份**不计入任何一栏**。"""
    out, used = [], []
    fw = bool(re.search(E1_FUTWORD, s, re.I))
    def _add(kind, tag, g, pos):
        out.append(("futq" if (kind == "fut" and _in(pos, qs)) else kind, tag, g, pos))
    for m in E1_FUTYEAR.finditer(s):
        y = int(m.group(1))
        if y > by + 74: continue
        if cpos and off + m.start() in cpos: continue      # 行内引注年不是时点（#48①）
        used.append((m.start(), m.end()))
        if y >= by + 1:
            _add("fut", "YEAR", m.group(0), off + m.start())
        elif y == by:
            _add("fut" if fw else "rec", "YEAR", m.group(0), off + m.start())
        else:
            _add("his", "YEAR", m.group(0), off + m.start())
    if not fw or not anchored:
        return out                    # 无未来指向词／无当下锚定：相对时点只是叙事时间
    for m in re.finditer(E1_RELTIME, s, re.I):
        if any(m.start() < y and x < m.end() for x, y in used):
            continue                  # 同句已被年份规则计过，不重复
        used.append((m.start(), m.end()))
        _add("fut", "RELTIME", m.group(0), off + m.start())
    return out

def fut_points_en(t, base_year=None, base_md=None):
    """① 英文版。与中文版同构：未来时点 = 未来年份，或**带具体时点**的 will/expected 句。
    收紧点：光有 will 不计（宣传体 will 满天飞，会把 +宣传+ 判成 +高下注+）。
    V37：改由 `_fut_in_sent_en` 出点数，与取消层同源（#41 的「文本层不动、可结算层降权」在英文侧才成立）。"""
    by = base_year or C1_NOW_YEAR
    bm, bd = base_md or C1_NOW_MD
    qs = _spans(t, E1_QSPAN); qs.sort()
    anchored = anchor_ok(t, by, E1_FUTYEAR, "en")   # V37 · A15（#48）：引注年／非正文行不作锚定
    cpos = cite_year_pos(t, "en")
    fut = futq = his = rec = 0
    for sm in _SENT_EN.finditer(t):
        for kind, _tag, _g, _pos in _fut_in_sent_en(sm.group(0), sm.start(), by, bm, bd, anchored, qs, cpos):
            if kind == "fut": fut += 1
            elif kind == "futq": futq += 1
            elif kind == "rec": rec += 1
            else: his += 1
    return fut, futq, rec, his

def channel_scores_en(t):
    r = {"_lang": "en", "_unit": "千词"}
    f_, fq_, rc_, h_ = fut_points_en(t)
    r["1_fut_n"], r["1_fut_qn"], r["1_rec_n"], r["1_his_n"] = f_, fq_, rc_, h_
    r["1_fut"] = f_ * 1000.0 / nw(t)
    r["1_fut_q"] = fq_ * 1000.0 / nw(t)
    r["1_rec"] = rc_ * 1000.0 / nw(t)
    r["1_his"] = h_ * 1000.0 / nw(t)
    r["1_dens"] = dens_en(t, [E1_RELTIME, r"\b20\d{2}\b"])
    r["1_action"] = 0.0
    _fc = fut_cancel_points(t, lang="en")          # V37 · A14（#51）：英文侧此前**没有**这条闸门
    r["1_fut_cancel_n"] = sum(1 for _k, _t, _g, _p in _fc if _k == "fut")
    r["1_fut_qcancel_n"] = sum(1 for _k, _t, _g, _p in _fc if _k == "futq")
    r["1_fut_cancel_list"] = [g for _k, _t, g, _p in _fc]
    r["1_fut_settle_n"] = max(0, f_ - r["1_fut_cancel_n"])   # 可结算层 = 文本层 −「已取消计划」
    r["1_anchor"] = 1.0 if anchor_ok(t, C1_NOW_YEAR, E1_FUTYEAR, "en") else 0.0
    r["2_quote"] = dens_en(t, E2_QUOTE)
    r["2_cite"] = dens_en(t, E2_CITE)
    r["3_def"] = r["3_def_bai"] = dens_en(t, E3_DEF)
    r["3_arg"] = r["3_arg_bai"] = dens_en(t, E3_ARG)
    r["3_def_wen"] = r["3_arg_wen"] = 0.0
    r["3_def_lun"] = r["3_def_jing"] = 0.0
    r["3_arg_causal"] = r["3_arg_revise"] = r["3_arg_add"] = 0.0
    r["3_reg"] = "英文体"
    r["4_forbid"] = r["4_forbid_bai"] = rawc_en(t, E4_FORBID)
    r["4_grade"] = rawc_en(t, E4_GRADE)
    r["4_forbid_d"] = dens_en(t, E4_FORBID)
    r["4_forbid_q"] = 0
    r["4_forbid_s"] = r["4_forbid_n"] = 0
    r["4_forbid_bai_q"] = 0
    r["4_talk"] = r["4_talk_q"] = 0
    r["gen_head"], r["gen_head_n"] = header_ratio(t)
    r["gen_dup"], r["gen_sent"], r["gen_uniq"] = repeat_ratio(t)
    r["selfref"] = []
    return r

def verdict_en(sc):
    v = []
    ff, hh, fq, rc = sc["1_fut"], sc["1_his"], sc["1_fut_q"], sc["1_rec"]
    fcn = sc.get("1_fut_cancel_n", 0)                              # V37 · A14（#51）
    fsn = sc.get("1_fut_settle_n", sc.get("1_fut_n", 0))           # 可结算层
    tail = "｜引述内 %d 处（引号）" % sc["1_fut_qn"] if sc["1_fut_qn"] else ""
    past = "｜本文时间线 %d 处（%.2f/千词）" % (sc["1_rec_n"], rc) if sc["1_rec_n"] else "｜历史引用 %.2f/千词" % hh
    cnote = "｜其中 %d 处为**已取消计划**（文本层真、可结算层剔除）" % fcn if fcn else ""
    if fsn >= 2 and ff >= 0.5:
        v.append(("① 命中", "**可结算**——有本文自己的未来时点", "未来时点 %d 处（%.2f/千词）%s%s%s" % (fsn, ff, tail, past, cnote)))
    elif ff > 0 or fq > 0:
        if ff == 0 and fq > 0: why = "只见引述的未来时点（他人承诺／引语），本文自己无可结算时点"
        elif fcn > 0 and fsn == 0: why = "未来时点 %d 处**全部是已取消计划**（文本层真、可结算层为 0）" % sc.get("1_fut_n", 0)
        elif fsn < 2: why = "孤立 %d 处未来时点，不足 2 处" % fsn
        else: why = "未来时点 %d 处但稀疏（%.2f/千词 < 0.5）" % (fsn, ff)
        v.append(("① 命中", "弱——%s" % why, (tail + past).lstrip("｜")))
    else:
        _m = "过去时点：" + past.lstrip("｜") if (hh >= 0.5 or rc >= 0.5) else "恒为 0"
        if sc["1_anchor"] < 1: _m += "｜**无当下锚定**（文无 ≥基准年-2 的年份；相对时点按叙事时间处理）"
        v.append(("① 命中", "不可执行——未见可结算时点", _m))
    if sc["2_cite"] > 0 and sc["2_quote"] >= 4:
        v.append(("② 文献", "可执行——文本自带引注", "引号 %.2f ／ 引注 %.2f（每千词）" % (sc["2_quote"], sc["2_cite"])))
    elif sc["2_cite"] > 0:
        v.append(("② 文献", "半——只有零星引注", "引号 %.2f ／ 引注 %.2f（每千词）" % (sc["2_quote"], sc["2_cite"])))
    elif sc["2_quote"] >= 4:
        v.append(("② 文献", "弱——**只有引号、无引注**（引号测的是「谁在说话」，不可核）", "引号 %.2f ／ 引注 0.00（每千词）" % sc["2_quote"]))
    else:
        v.append(("② 文献", "弱——引号与引注都稀薄", "引号 %.2f ／ 引注 %.2f（每千词）" % (sc["2_quote"], sc["2_cite"])))
    form = "定义式" if sc["3_def"] > sc["3_arg"] else "论辩式"
    if sc["3_def"] + sc["3_arg"] < 0.30:
        v.append(("③ 自洽", "无标记——英文表也查不到判据（**不得据此判「不自洽」**）", "定义 %.2f ／ 论辩 %.2f（每千词）" % (sc["3_def"], sc["3_arg"])))
    else:
        v.append(("③ 自洽", "英文体·%s" % form, "定义 %.2f ／ 论辩 %.2f（每千词）｜英文表 V32 装，V37 起与取消层同源（A14／A15）" % (sc["3_def"], sc["3_arg"])))
    if sc["4_forbid"] >= 3 and sc["4_grade"] >= 3:
        v.append(("④ 信", "**命中（定罪级）**——文本自带禁止检验条款", "%d 处" % sc["4_forbid"]))
    elif sc["4_forbid"] >= 3:
        v.append(("④ 信", "**命中（劝信级）**", "%d 处" % sc["4_forbid"]))
    else:
        v.append(("④ 信", "未命中", "%d 处" % sc["4_forbid"]))
    return v

def _spans(t, pat):
    return [(m.start(), m.end()) for m in re.finditer(pat, t)]

def _in(i, spans):
    for s0, s1 in spans:
        if s0 <= i < s1:
            return True
        if i < s0:
            break
    return False

_SENT = re.compile(r"[^。！？；\n]+")
_SENT_EN = re.compile(r"[^.!?\n]+")
E1_CLAUSE = re.compile(r"[^,.;:!?\n]+")   # #41／#51 英文侧的共现窗口 = **分句**

# ---- V37 · A15（#48④／#49① **位置闸门**）----
# #47 只按「站点形制」判尾注区／表格区；本条改成**按位置判**，并把三层补齐：
#   ① **行内引注**——学术／论辩文本最密的那一层（`(Wang et al., 2015)`／`Robbe & Buzsaki 2009`）。
#      P3 实测：eLife 论辩稿 236 处命中里 234 处（99.2%）是参考文献年，未来时点 0 处；
#      「命中」这一格量到的是**参考文献量**。
#   ② **书前序跋／落款／版权页／作者自述**——P4 实测：《安史之乱》33.5 万汉字的「当下」全部来自
#      序（李碧妍 2023-08-14）、跋（张诗坪 2024-12-03）、版权页（2025 年 8 月第 1 版）
#      ＋DLC 脚注（2025-08-08）；一份讲 8 世纪的叙事文本因此被判「有当下锚定」，
#      于是「吐蕃**明年**就单独夺走了唐朝的甘州、肃州」（8 世纪的事）被读成本文的未来（#49 伪将来时）。
#   ③ **脚注／尾注行**（`[12] …`／`12. …`）。
# 这三层都**不作时点、也不作「当下锚定」的证据**（正文层与行内引注层的分工见 #47／#48）。
C1_YEARN = re.compile(r"(\d{4})\s*年")
# **收紧到「括号内」**：第一版把 `<大写词> 20XX` 一律判引注，实测把 `Fiscal 2027`／`January 2027`／
#   `H1 2027` 这类**真时点**一起吞掉（p1 未来时点 52→36、p2 28→16，见标定记录）——
#   故只有**落在圆括号内的**「作者＋年份」才算引注；行内裸提（`in 2027`／`the 2027 plan`）一律照算。
C1_CITE_PAREN = re.compile(r"\([^()\n]{0,400}\)")
C1_CITE_SIG = re.compile(
    # 月名／会计期间名／栏目名在前 ⇒ 不是作者名（`(Eur 110,123.00 as at 31 December 2023)` 实测：
    #   第一版把 Beretta 要约文件里 **78 处**「as at 31 December 20XX」的对账基准日当引注吞掉）。
    r"(?!(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Q[1-4]|H[12]|FY|Fiscal|Year|Note|Notes|Table|Section|Chapter|Quarter|First|Second|Third|Fourth|Value|Total|Amount|Price|Rate)\b)"
    r"(?:[A-Z][A-Za-z\u00c0-\u024f'\u2019\-\.]{1,}"
    r"(?:\s+(?:et\.?\s+al\.?|and|&)\s*[A-Za-z\u00c0-\u024f'\u2019\-]*)?\s*,?\s*)"
    r"(?:19|20)\d{2}(?!\d|\s*[\-\u2013]\s*\d)")
C1_CITE_EN_TEXT = re.compile(
    r"(?!(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Q[1-4]|H[12]|FY|Fiscal|Year|Note|Notes|Table|Section|Chapter|Quarter|First|Second|Third|Fourth|Value|Total|Amount|Price|Rate)\b)"
    r"[A-Z][A-Za-z\u00c0-\u024f'\u2019\-\.]{1,}"
    r"(?:\s+(?:et\.?\s+al\.?|and|&)\s*[A-Za-z\u00c0-\u024f'\u2019\-]*)?"
    r"\s*\(\s*((?:19|20)\d{2})[a-z]?\s*\)")
# **中文引注收紧到经典形制 `（作者，年）`**：第一版（任意内容＋逗号＋年）在实测里
#   把三类**正文**一起吞掉——`（黄晓明 2008 年 3 元/股…，2009 上市后…）`、
#   `（换来 1937 年二次衰退，1941 年参战才摆脱大萧条）`、`（上海，1949）`。
#   收紧后只认「2–6 个汉字 ＋ 逗号 ＋ 年份 ＋ 立刻收括号」，上述三类一律不动（0 真损失）。
C1_CITE_ZH = re.compile(
    r"[\uff08(]\s*[\u4e00-\u9fff\u00b7]{2,6}\s*[\uff0c,]\s*((?:19|20)\d{2})\s*[\uff09)]")
C1_SIGN_LINE = re.compile(
    r"^[ \t]*[\u4e00-\u9fff\u00b7]{2,8}[ \t]*\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?[ \t]*$", re.M)
C1_COLOPHON_LINE = re.compile(
    r"^[^\n]{0,240}?(?:出品人|策划编辑|责任编辑|营销编辑|出版发行|印张|字数\s*[:：]|定价\s*[:：]|开本\s*[:：]"
    r"|I\s*S\s*B\s*N|C\s*I\s*P\s*数据|第\s*\d+\s*版|次印刷)[^\n]{0,240}?$", re.M)
C1_NOTE_LINE = re.compile(r"^[ \t]*\[?\d{1,3}[\]\u3001\.\uff09)]\s*\S[^\n]{0,400}$", re.M)
def nonbody_spans(t):
    """非正文要素区（**位置闸门** · #47／#48／#49）：落款行／版权页行／脚注尾注行。"""
    out = []
    for pat in (C1_SIGN_LINE, C1_COLOPHON_LINE, C1_NOTE_LINE):
        for m in pat.finditer(t):
            out.append((m.start(), m.end()))
    out.sort()
    return out
def cite_year_pos(t, lang=None):
    """行内引注年的**绝对偏移**集合（#48①）：英文 `<作者>[,] <年>`（含 `et al.`）＋中文 `（<作者>，<年>）`。"""
    pos = set()
    if lang == "en":
        for pm in C1_CITE_PAREN.finditer(t):
            g = pm.group(0)
            if not C1_CITE_SIG.search(g):
                continue
            for ym in re.finditer(r"(?:19|20)\d{2}", g):
                pos.add(pm.start() + ym.start())
        for m in C1_CITE_EN_TEXT.finditer(t):
            pos.add(m.start(1))
    for m in C1_CITE_ZH.finditer(t):
        pos.add(m.start(1))
    return pos
def anchor_ok(t, by, pat_years, lang=None):
    """「当下锚定」的证据必须来自**正文**、且不是**引注年**（#48）：引注年与书前书后落款
    都不证明「本文以现在为参照」。"""
    nb = nonbody_spans(t)
    cpos = cite_year_pos(t, lang)
    for m in pat_years.finditer(t):
        y = int(m.group(1))
        if not (by - 2 <= y <= by):
            continue
        if m.start() in cpos or _in(m.start(), nb):
            continue
        return True
    return False


# V34 · A9（#39 **归档索引区**）：刊头闸门只认前 40 行，而「往期目录」能长到几十行，
#   形制也特殊——`NN周X <标题>`（序号＋星期开头的目录条目）、`……查看完整日报归档`。
#   A-1 多期回归实测（AIHOT 09-24～09-30 共 7 期）：每期漏 4 处（含「推进 2028 年 3 月自动化 AI 研究员」
#   这类**往期标题里的未来年**），7/7 期同形 ⇒ 判据：目录条目**成串出现（≥3 连续）**才算，
#   孤例不算（避免把正文里「3 周一」误伤）。
C1_ARCH_IDX  = re.compile(r"^\s*\d{1,2}\s*周[一二三四五六日]\s*\S")
C1_ARCH_LINK = re.compile(r"(?:查看完整日报归档|日报归档|合订本)\s*$|^\s*往期\s*$")
# V36 · A11（#42 **尾栏导航区**）：`前一日 · <日期>`／`后一日 · <日期>` 行及其后紧邻的「次日标题」行，
#   与刊头（#38）、往期目录（#39）同族，都是**版面信息**；却既不在前 40 行（#38 够不着）、
#   也不是 `NN周X` 形制（#39 够不着）⇒ 漏检。实测（AIHOT 30 期）：09-06／09-08 各漏 1 处
#   「推进 2028 年 3 月自动化 AI 研究员」（被记成**本文的**未来时点），09-28／09-30 各多计 1 处历史年。
#   全库（本机标定语料库，393 份）命中 **0**——对库内语料惰性，与 #38／#39 同类。
C1_NAV = re.compile(r"^\s*(?:前一日|后一日|上期|下期|前一期|后一期)\s*[·・:：]")
def archive_spans(t):
    """归档索引区的字符区间（**不限行号**，与 masthead_spans 互补）。"""
    lines = t.split("\n")
    offs, o = [], 0
    for ln in lines:
        offs.append(o); o += len(ln) + 1
    spans, run = [], []
    def _flush():
        if len(run) >= 3:
            for k in run:
                spans.append((offs[k], offs[k] + len(lines[k]) + 1))
    for i, ln in enumerate(lines):
        if C1_ARCH_IDX.match(ln):
            if run and i == run[-1] + 1: run.append(i)
            else: _flush(); run = [i]
        else:
            _flush(); run = []
    _flush()
    for i, ln in enumerate(lines):
        if C1_ARCH_LINK.search(ln):
            spans.append((offs[i], offs[i] + len(lines[i]) + 1))
    for i, ln in enumerate(lines):          # V36 · A11（#42）：尾栏导航区
        if C1_NAV.match(ln) or ("前一日" in ln) or ("后一日" in ln):
            j = i
            while j < len(lines) and j - i <= 3 and not lines[j].strip().startswith("（本期完）"):
                spans.append((offs[j], offs[j] + len(lines[j]) + 1))
                j += 1
    spans.sort()
    return spans
# V34 · A8（#40 **回顾式相对时点**）：时长型（`本季度`／`N个月内`／`窗口期`）**自身不含未来指向**
#   ——「本季度以来增长超 70%」「GPU 租金在九个月内翻倍」都是回顾。全库实测：时长型命中 9 处，
#   其中 6 处无前瞻词、逐条核验 **6/6 全是回顾式**（货币战争 2／5、巫师财经、AI 圆桌）；
#   装「须前瞻同现」后 9→3。**本色前瞻**（明年／下月／未来N年／年底前）不在闸门内。
C1_RELTIME_DUR = re.compile(r"(?:本季度|窗口期|[一二三四五六七八九十\d]{1,3}(?:個月|个月|週|周|交易日)(?:內|内))")
C1_FORECAST    = re.compile(r"將|将|即將|即将|預計|预计|預期|预期|預測|预测|展望|有望|計劃|计划|擬|拟|打算|準備|准备|未來|未来|明年|下月|年底|之后|之後|今后|今後|目標|目标|承諾|承诺|會在|会在|將會|将会|將在|将在|或將|或将")
def _qs_of(t):
    """① 的引述区间（引号／反引号 ＋ 引述框架），_in() 要求有序。"""
    qs = _spans(t, C1_QSPAN)
    for _m in re.finditer(C1_ATTR, t):        # 引述框架：从标记到句末
        _e = len(t)
        for _tm in "。！？；":
            _q = t.find(_tm, _m.end())
            if _q > 0: _e = min(_e, _q + 1)
        qs.append((_m.start(), min(_e, _m.end() + 140)))
    qs.sort()
    return qs

def _fut_in_sent(s, off, by, bm, bd, anchored, inst, qs, cpos=None):
    """**单句**内的时点枚举——① 的公共内核。返回 [(kind, tag, text, pos)]，kind ∈ {fut, futq, rec, his}。
    fut_points（文本层）与 fut_cancel_points（取消层）共用本函数，保证两层判据同源、不漂移。"""
    out, used = [], []
    fw = bool(re.search(C1_FUTWORD, s))
    def _add(kind, tag, g, pos):
        out.append(("futq" if (kind == "fut" and _in(pos, qs)) else kind, tag, g, pos))
    for m in re.finditer(C1_RELTIME, s):
        used.append((m.start(), m.end()))
        if not anchored: continue      # 无当下锚定：相对时点只是叙事时间
        if C1_RELTIME_DUR.match(m.group(0)):
            if not C1_FORECAST.search(s):
                continue               # V34 · A8（#40）：时长型须前瞻同现，否则是回顾式
            if "了" in s[m.end():m.end() + 12]:
                continue               # V36 · A12（#43）：时长型＋已完成「了」⇒ 回顾（非未来）
        _add("fut", "RELTIME", m.group(0), off + m.start())
    for m in re.finditer(r"(\d{4})\s*年", s):
        y = int(m.group(1))
        if cpos and off + m.start() in cpos: continue   # V37 · A15（#48①）：行内引注年不是时点
        if y > by + 74:            # 5000年文明史一类：年代跨度，非时点
            continue
        md = C1_YEAR_MD.match(s, m.end())
        mdv = (int(md.group(1)), int(md.group(2) or 1)) if md else None
        if y >= by + 1 or (y == by and (fw or (mdv and mdv > (bm, bd)))):
            _add("fut", "YEAR", m.group(0), off + m.start())
        elif y == by:
            _add("rec", "YEAR", m.group(0), off + m.start())
        else:
            _add("his", "YEAR", m.group(0), off + m.start())
    for m in C1_YEAR_DASH.finditer(s):
        if cpos and off + m.start() in cpos: continue   # V37 · A15（#48①）
        y, mo, dy = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
        if mo > 12 or y > by + 74:      # V37 · A13（#50）：月份合法性（原代码误写作 `mo > by + 74`，等于不校验）
            continue
        if y >= by + 1 or (y == by and ((mo, dy) > (bm, bd) or fw)):
            _add("fut", "DASH", m.group(0), off + m.start())
        elif y == by:
            _add("rec", "DASH", m.group(0), off + m.start())
        else:
            _add("his", "DASH", m.group(0), off + m.start())
    for _mr, _abbr in ((C1_YEAR_RANGE, False), (C1_YEAR_RANGE2, True)):
        for m in _mr.finditer(s):           # V37 · A13 配套：**年份区间**（不是「年-月」）
            if cpos and off + m.start() in cpos: continue
            y = int(m.group(1))
            if _abbr and int(m.group(2)) < (y % 100):
                continue                    # 缩写区间的尾两位数必须 ≥ 年尾两位数，否则是日期（`2026-12`）
            if y > by + 74: continue
            if any(m.start() < y2 and x2 < m.end() for x2, y2 in used):
                continue                    # 已被年份／年-月规则计过，不重复
            used.append((m.start(), m.end()))
            if y >= by + 1 or (y == by and fw):
                _add("fut", "YRANGE", m.group(0), off + m.start())
            elif y == by:
                _add("rec", "YRANGE", m.group(0), off + m.start())
            else:
                _add("his", "YRANGE", m.group(0), off + m.start())
    for m in C1_MD_DEAD.finditer(s):        # V33 · A3：无年份月日（期限标记＋晚于基准日）

        if not anchored: continue
        mo, dy = int(m.group(1)), int(m.group(2) or 1)
        if mo > 12: continue
        if (mo, dy) <= (bm, bd): continue
        if any(m.start() < y and x < m.end() for x, y in used):
            continue
        used.append((m.start(), m.end()))
        _add("fut", "MD_NYR", m.group(0), off + m.start())
    if inst:
        for m in re.finditer(C1_SCHED, s):
            tm = re.search(C1_SCHED_TIME, s[m.end():m.end() + 40])
            if not tm: continue
            a = m.end() + tm.start()
            if any(a < y and x < a + tm.end() - tm.start() for x, y in used):
                continue          # 已被年份／相对时点规则计过，不重复
            _add("fut", "SCHED", s[m.start():a + tm.end() - tm.start()], off + a)
    return out

def fut_points(t, base_year=None, base_md=None):
    """① 时点（**文本层**）。返回 (未来时点, 引述内未来时点, 本文时间线, 历史引用)。
    未来时点 = (a) 未来年份（年份 >= 基准年+1，自带未来指向）
              (b) 基准年当年但**月日尚未到**的具体日期（2026-12-31）
              (c) 相对时点（未来N年／明年／下季度／窗口期…）
    本文时间线 = 基准年当年、月日已过（流水账条目一类：本文自身的记录）
    历史引用   = 年份 <= 基准年-1
    落在引号／反引号内的未来时点并入第 2 项：他人承诺 != 本文判断。
    **V35 起**：本函数只算**文本层**；「已取消计划」的降权在 fut_cancel_points（#41）。"""
    by = base_year or C1_NOW_YEAR
    bm, bd = base_md or C1_NOW_MD
    qs = _qs_of(t)
    anchored = anchor_ok(t, by, C1_YEARN, "zh")   # V37 · A15（#48④／#49①）：引注年与书前书后落款不作锚定
    cpos = cite_year_pos(t, "zh")                  # V37 · A15（#48①）
    inst = is_institutional(t)                        # V32 体裁闸门（A-1）
    hd = masthead_spans(t) + archive_spans(t)         # V33 刊头闸门（A1 #38）＋ V34 归档索引区（A9 #39）
    fut = futq = his = rec = 0
    for sm in _SENT.finditer(t):
        off = sm.start()
        if _in(off, hd): continue     # 刊头行整句不计（发刊日期／期号／刊名／文首标题）
        for kind, _tag, _g, _pos in _fut_in_sent(sm.group(0), off, by, bm, bd, anchored, inst, qs, cpos):
            if kind == "fut": fut += 1
            elif kind == "futq": futq += 1
            elif kind == "rec": rec += 1
            else: his += 1
    return fut, futq, rec, his

# V35 · A10（#41 **取消式计划**）：未来时点若落在**取消／废弃类**动词所在的句子里，文本层仍是真时点，
#   但**可结算层必须降权**——「已取消的计划」不构成可结算的未来断言。
#   实测触发（A-2 多期回归）：AIHOT 2026-09-30「OpenAI **取消**原定**下月**发布 GPT-6.1 的计划」
#   同形 3 处，V34 全记成 fut（文本层真、可结算层应为 0）。
C1_CANCEL = re.compile(
    r"取消|撤銷|撤销|撤除|廢除|废除|作廢|作废|放棄|放弃|終止|终止|叫停|擱置|搁置|夭折|流產|流产"
    # V37 · A14（#51 ①）：**词表缺口**——真实的中文取消文书写的不是「取消」而是「废标」。
    #   P5 实测：10 份政府采购**废标公告**（正文「八、废标理由：…」），#41 命中 **0**；
    #   单份汉字 750–889，C1_CANCEL 全文命中 **0 份（10/10 全不命中）**。
    r"|廢標|废标|流標|流标|中止招標|中止招标|終止招標|终止招标|不予採購|不予采购|採購失敗|采购失败|撤項|撤项"
    r"|不再(?:推進|推进|舉行|举行|發布|发布|計劃|计划|發售|发售|上線|上线)"
    r"|無限期中止|无限期中止|無限期擱置|无限期搁置")
# V37 · A14（#51 ②）：**英文取消词表**——V36 之前英文侧**根本没有这条闸门**
#   （`fut_cancel_points` 用的 `_SENT`／`C1_CANCEL`／`C1_CLAUSE` 全是中文专用）。
C1_CANCEL_EN = re.compile(
    r"\b(?:cancel(?:s|led|ling)?|cancell(?:ed|ing)|withdraw(?:s|n|ing)?|withdrew"
    r"|terminat(?:e|es|ed|ing|ion|ions)|discontinu(?:e|es|ed|ing)"
    r"|abandon(?:s|ed|ing)?|scrap(?:s|ped|ping)?|call(?:s|ed|ing)? off"
    r"|pull(?:s|ed|ing)? the plug|no longer (?:intend|plan|expect|pursue)s?|walk(?:s|ed|ing)? away from)\b", re.I)
C1_CLAUSE = re.compile(r"[^，,、。！？；：\n]+")   # #41 的共现窗口 = **分句**（不是整句）
def fut_cancel_points(t, base_year=None, base_md=None, lang="zh"):
    """#41：落在**取消式分句**里的未来时点清单，返回 [(kind, tag, text, pos)]（kind ∈ {fut, futq}）。
    文本层计数不动（这正是「文本层是真时点」）；可结算层 = fut_points − 本项。
    **窗口 = 同分句**（C1_CLAUSE）：取消动词必须与该时点落在同一分句内。
    实测：全库同句共现只 1 处 = 货币战争2「政府放弃了国有化…支撑了未来7周的战争」
    ——取消与未来时点分属不同分句，是假阳性；收紧到分句后 1→0，而对 AIHOT
    「OpenAI 取消原定下月发布 GPT-6.1 的计划」3 处（取消与「下月」同一分句）零损失。
    **V37 · A14（#51）**：本函数改为**语种无关**——`lang="en"` 走 `_SENT_EN` ＋ `C1_CANCEL_EN` ＋ `E1_CLAUSE`
      ＋ `_fut_in_sent_en`；在此之前英文侧**根本没有这条闸门**（P5 实测：FTC《Withdraws…》与 ctgov
      TERMINATED／WITHDRAWN 共 21 份，取消层从未产出过一项——但**本轮语料上造不出真损失**，
      因为一个**已被终止**的项目，它的文书**通体是回顾式**：① 在它们上面产出 0 处未来时点，闸门无从下手）。
    **V37 · A15**：锚定与引注年同 ① 口径（#48）——引注年不作锚定、也不充作取消层时点。"""
    by = base_year or C1_NOW_YEAR
    bm, bd = base_md or C1_NOW_MD
    en = (lang == "en")
    if en:
        qs = _spans(t, E1_QSPAN); qs.sort()
        anchored = anchor_ok(t, by, E1_FUTYEAR, "en")
        sent, cancel, clause = _SENT_EN, C1_CANCEL_EN, E1_CLAUSE
    else:
        qs = _qs_of(t)
        anchored = anchor_ok(t, by, C1_YEARN, "zh")
        sent, cancel, clause = _SENT, C1_CANCEL, C1_CLAUSE
    cpos = cite_year_pos(t, lang)
    inst = is_institutional(t)
    hd = masthead_spans(t) + archive_spans(t)
    out = []
    for sm in sent.finditer(t):
        off = sm.start()
        if _in(off, hd): continue
        s = sm.group(0)
        if not cancel.search(s): continue
        if en:
            hits = [h for h in _fut_in_sent_en(s, off, by, bm, bd, anchored, qs, cpos) if h[0] in ("fut", "futq")]
        else:
            hits = [h for h in _fut_in_sent(s, off, by, bm, bd, anchored, inst, qs, cpos) if h[0] in ("fut", "futq")]
        if not hits: continue
        cls = [(c.start(), c.end()) for c in clause.finditer(s) if cancel.search(c.group(0))]
        for kind, tag, g, pos in hits:
            rel = pos - off
            if any(c0 <= rel < c1 for c0, c1 in cls):
                out.append((kind, tag, g, pos))
    return out

def dens(t, pats):
    n = nk(t); return sum(len(re.findall(p, t, re.M)) for p in pats) * 1000.0 / n
def rawc(t, pats):
    return sum(len(re.findall(p, t, re.M)) for p in pats)

def auto_selfref(t):
    """查自指：找形如「若X，云何…」「X亦復X」的自我消解句式"""
    pats = [r"若[^。？]{2,20}，云何[^。？]{2,30}", r"亦復[^。，]{0,4}空", r"因言遣言", r"應無所住"]
    out = []
    for p in pats:
        for m in re.finditer(p, t):
            out.append(m.group(0)[:40])
            if len(out) >= 3: return out
    return out

def repeat_ratio(t):
    """长句重复率（#23）"""
    sents = [s.strip() for s in re.split(r"[。！？\n]", t) if len(s.strip()) >= 20]
    if not sents: return 0.0, 0, 0
    c = collections.Counter(sents)
    dup = sum(v for v in c.values() if v > 1)
    return dup / len(sents), len(sents), len(c)

def header_ratio(t):
    lines = t.split("\n")
    if not lines: return 0.0, 0
    h = 0
    for l in lines:
        for p in GEN_HEAD:
            if re.search(p, l): h += 1; break
    return h / len(lines), h

def channel_scores(t):
    r = {}
    r["1_dens"] = dens(t, C1_DATE)
    f_, fq_, rc_, h_ = fut_points(t)
    r["1_fut_n"], r["1_fut_qn"], r["1_rec_n"], r["1_his_n"] = f_, fq_, rc_, h_
    _fc = fut_cancel_points(t)                    # V35 · A10（#41）：取消式计划
    r["1_fut_cancel_n"] = sum(1 for _k, _t, _g, _p in _fc if _k == "fut")
    r["1_fut_qcancel_n"] = sum(1 for _k, _t, _g, _p in _fc if _k == "futq")
    r["1_fut_cancel_list"] = [g for _k, _t, g, _p in _fc]
    r["1_fut_settle_n"] = max(0, f_ - r["1_fut_cancel_n"])   # 可结算层 = 文本层 −「已取消计划」
    r["1_fut"] = f_ * 1000.0 / nk(t)
    r["1_fut_q"] = fq_ * 1000.0 / nk(t)
    r["1_rec"] = rc_ * 1000.0 / nk(t)
    r["1_his"] = h_ * 1000.0 / nk(t)
    r["1_anchor"] = 1.0 if anchor_ok(t, C1_NOW_YEAR, C1_YEARN, "zh") else 0.0   # V37 · A15（#48④／#49①）
    r["1_future"] = dens(t, C1_FUTURE)
    r["1_action"] = dens(t, C1_ACTION)
    r["2_quote"] = dens(t, C2_QUOTE)
    r["2_cite"] = dens(t, C2_CITE)
    def_lun, def_jing = dens(t, C3_DEF_LUN), dens(t, C3_DEF_JING)
    def_wen, arg_wen = def_lun + def_jing, dens(t, C3_ARG)
    r["3_def_lun"], r["3_def_jing"] = def_lun, def_jing
    def_bai = dens(t, C3_DEF_BAI)
    arg_c, arg_r, arg_a = dens(t, C3_ARG_BAI), dens(t, C3_ARG_BAI_R), dens(t, C3_ARG_BAI_A)
    r["3_arg_causal"], r["3_arg_revise"], r["3_arg_add"] = arg_c, arg_r, arg_a
    arg_bai = arg_c + arg_r + arg_a
    r["3_def_wen"], r["3_arg_wen"] = def_wen, arg_wen
    r["3_def_bai"], r["3_arg_bai"] = def_bai, arg_bai
    # 文体判定：两套表谁占优；都稀薄则记为无标记
    wen, bai = def_wen + arg_wen, def_bai + arg_bai
    if wen + bai < 0.30:
        reg = "无标记"
    elif bai > 2 * wen:
        reg = "白话体"
    elif wen > 2 * bai:
        reg = "译经体" if def_jing > def_lun else "论书体"
    else:
        reg = "混合体"
    r["3_reg"] = reg
    # 对齐：主判据取与文体匹配的那一套（#28 的核心）
    if reg in ("论书体", "译经体"):
        r["3_def"], r["3_arg"] = def_wen, arg_wen
    else:
        r["3_def"], r["3_arg"] = def_bai, arg_bai
    r["4_forbid"] = rawc(t, C4_FORBID)
    r["4_grade"] = rawc(t, C4_GRADE)
    r["4_forbid_d"] = dens(t, C4_FORBID)
    _qs4 = _spans(t, C1_QSPAN); _qs4.sort()   # 引号 span（_in 要求有序）
    r["4_forbid_q"] = sum(1 for x in C4_FORBID for _m in re.finditer(x, t) if _in(_m.start(), _qs4))
    # #33 同形异域：白话体侧的等效计数（强核 ＋「不信＋教义宾语」）
    r["4_forbid_s"] = rawc(t, C4_FORBID_S)
    r["4_forbid_n"] = rawc(t, C4_FORBID_N)
    r["4_forbid_bai"] = rawc(t, C4_FORBID_BAI)
    r["4_forbid_bai_q"] = (sum(1 for x in C4_FORBID_S for _m in re.finditer(x, t) if _in(_m.start(), _qs4))
                           + sum(1 for x in C4_FORBID_N for _m in re.finditer(x, t) if _in(_m.start(), _qs4)))
    r["4_talk"] = rawc(t, C4_TALK)
    r["4_talk_q"] = sum(1 for x in C4_TALK for _m in re.finditer(x, t) if _in(_m.start(), _qs4))
    r["gen_head"], r["gen_head_n"] = header_ratio(t)
    r["gen_dup"], r["gen_sent"], r["gen_uniq"] = repeat_ratio(t)
    r["selfref"] = auto_selfref(t)
    return r

def verdict(sc):
    if sc.get("_lang") == "en":
        return verdict_en(sc)
    v = []
    # ① 是否可结算
    ff, hh, fq, rc = sc.get("1_fut", 0), sc.get("1_his", 0), sc.get("1_fut_q", 0), sc.get("1_rec", 0)
    fqn, rcn = sc.get("1_fut_qn", 0), sc.get("1_rec_n", 0)
    fcn = sc.get("1_fut_cancel_n", 0)                            # V35 · A10（#41）
    fsn = sc.get("1_fut_settle_n", sc.get("1_fut_n", 0))         # 可结算层
    tail = "｜引述内 %d 处（引号／引述框架）" % fqn if fqn else ""
    past = "｜本文时间线 %d 处（%.2f/千字）" % (rcn, rc) if rcn else "｜历史引用 %.2f/千字" % hh
    cnote = "｜其中 %d 处为**已取消计划**（文本层真、可结算层剔除）" % fcn if fcn else ""
    anchored = sc.get("1_anchor", 0) >= 1
    if ff >= 0.5 and fsn >= 2:
        v.append(("① 命中", "**可结算**——有本文自己的未来时点",
                  "未来时点 %d 处（%.2f/千字）%s%s%s" % (fsn, ff, tail, past, cnote)))
    elif ff > 0 or sc["1_action"] >= 0.2 or fq > 0:
        if ff == 0 and fq > 0:
            why = "只见引述的未来时点（他人承诺／政策文件／公告），本文自己无可结算时点"
        elif fcn > 0 and fsn == 0:
            why = "未来时点 %d 处**全部是已取消计划**（文本层真、可结算层为 0）" % sc.get("1_fut_n", 0)
        elif ff > 0 and fsn < 2:
            why = "孤立 %d 处未来时点，不足 2 处，不成其为可结算断言" % fsn
        elif ff > 0:
            why = "未来时点 %d 处但稀疏（%.2f/千字 < 0.5）：有可核的时点，密度撑不起一篇判断" % (sc.get("1_fut_n", 0), ff)
        else:
            why = "有行动指令但无时点"
        v.append(("① 命中", "弱——%s" % why, (tail + past).lstrip("｜")))
    else:
        _msg = "过去时点：" + past.lstrip("｜") if (hh >= 0.5 or rc >= 0.5) else "恒为 0"
        if not anchored:
            _msg += "｜**无当下锚定**（文中无 ≥基准年-2 的绝对年份；相对时点按叙事时间处理）"
        v.append(("① 命中", "不可执行——未见可结算时点", _msg))

    # ②
    # #32 同族：**引号不是引证**。全库 393 份里引注>0 的只有 11 份；旧判据"引号≥4 ⇒ 可执行"把 190 份
    #   判成可执行，其中绝大多数是**对白／经文／术语言说的引号**（救世主 12.64／论语详解 47.50／AI圆桌 18.98），真引注 = 0。
    #   另一处：旧条件里的 `2_cite >= 2` 是**死条款**——引注密度全库上限只有 0.208/千字，永远到不了 2（#30 同族：把「处数」的阈值
    #   用在了「密度」上）。故改为「引注>0 才算有外证」。此后 ② 的分布 190 可执行 → **9 可执行／2 半／382 弱**。
    if sc["2_cite"] > 0 and sc["2_quote"] >= 4:
        v.append(("② 文献", "可执行——文本自带引注（书名／卷页／参见）",
                  "引号 %.2f ／ 引注 %.2f（每千字）" % (sc["2_quote"], sc["2_cite"])))
    elif sc["2_cite"] > 0:
        v.append(("② 文献", "半——只有零星引注",
                  "引号 %.2f ／ 引注 %.2f（每千字）" % (sc["2_quote"], sc["2_cite"])))
    elif sc["2_quote"] >= 4:
        v.append(("② 文献", "弱——**只有引号、无引注**（引号是文体习惯：对白／经文／术语，不可核）",
                  "引号 %.2f ／ 引注 0.00（每千字）" % sc["2_quote"]))
    else:
        v.append(("② 文献", "弱——引号与引注都稀薄",
                  "引号 %.2f ／ 引注 %.2f（每千字）" % (sc["2_quote"], sc["2_cite"])))
    # ③
    form = "定义式" if sc["3_def"] > sc["3_arg"] else "论辩式"
    if sc.get("3_def_wen", 0) + sc.get("3_arg_wen", 0) + sc.get("3_def_bai", 0) + sc.get("3_arg_bai", 0) < 0.30:
        v.append(("③ 自洽", "无标记——两套表都查不到判据（**不得据此判「不自洽」**）",
                  "文 %.2f/白 %.2f（每千字）" % (sc.get("3_def_wen", 0) + sc.get("3_arg_wen", 0),
                                                sc.get("3_def_bai", 0) + sc.get("3_arg_bai", 0))))
    else:
        split = ""
        if sc.get("3_reg") in ("论书体", "译经体"):
            split = "（论书 %.2f／译经 %.2f）" % (sc.get("3_def_lun", 0), sc.get("3_def_jing", 0))
        v.append(("③ 自洽", "可执行（%s · %s）" % (sc.get("3_reg", "?"), form),
                  "对齐表 定义 %.2f%s / 论证 %.2f（因果 %.2f／纠偏 %.2f／递进 %.2f）｜ 另表 文 %.2f/白 %.2f（每千字）" % (
                      sc["3_def"], split, sc["3_arg"],
                      sc.get("3_arg_causal", 0), sc.get("3_arg_revise", 0), sc.get("3_arg_add", 0),
                      sc.get("3_def_wen", 0) + sc.get("3_arg_wen", 0),
                      sc.get("3_def_bai", 0) + sc.get("3_arg_bai", 0))))
    # ④
    fbn, fbq = sc.get("4_forbid", 0), sc.get("4_forbid_q", 0)
    tkn, tkq = sc.get("4_talk", 0), sc.get("4_talk_q", 0)
    reg4 = sc.get("3_reg", "?")
    fbr = 0.0 if fbn == 0 else fbq / fbn
    # #33 同形异域：白话体里 `不信` 是世俗态度（「年轻人不信未来」），不是教义条款；
    #   故白话体改用**等效计数**（强核 ＋ 不信＋教义宾语）；译经／论书体仍用宽表。
    if reg4 == "白话体":
        fbne, fbqe = sc.get("4_forbid_bai", fbn), sc.get("4_forbid_bai_q", fbq)
    else:
        fbne, fbqe = fbn, fbq
    # #32 文体闸门：引号占比单用会**误伤经**——
    #   经（T0223 摩訶般若 86%／T0670 楞伽 94%）里「」包的是**佛说本身**，即教义，不是人物对白；
    #   小说（救世主 71%／天幕红尘 90%）里「」包的才是人物之口。
    #   故降级只在**白话体**上启用；译经体／论书体（教义性文本）不降级。
    if fbn >= 3 and fbr >= 0.60 and reg4 == "白话体":
        v.append(("④ 信", "↘ 降级——禁止语多出**人物之口**（引文／对白），非文本自带的条款",
                  "%d 处，其中引号内 %d（%.0f%% ≥ 60%%）且文体=%s ⇒ 须回原文看有无**叙述层**条款" % (fbn, fbq, 100*fbr, reg4)))
    elif fbne >= 3:
        grade = "定罪级" if sc["4_grade"] >= 3 else "劝信级"
        _gt = "（引号内 %.0f%%，但文体=%s ⇒ 引号即教义本身，不降级）" % (100*fbr, reg4) if fbr >= 0.60 else ""
        v.append(("④ 信", "命中——文本自带禁止检验条款",
                  "%d 处%s，判定强度：%s" % (fbne, _gt, grade)))
    else:
        _t33 = "" if fbne == fbn else "；**#33 白话体等效 %d 处**（宽表 %d 处里的「不信」是世俗态度，已剔）" % (fbne, fbn)
        v.append(("④ 信", "未命中", "%d 处（引号内 %d）%s" % (fbn, fbq, _t33)))
    if tkn >= 3:
        tkr = tkq / tkn
        if tkr >= 0.60:
            v.append(("④ 信·体裁边界", "⚠ 疑似**人物回避**，非教义性禁止检验——本条不得用于判 ④",
                      "话题切换 %d 处，引号内 %d（%.0f%%）：回避发生在对白里" % (tkn, tkq, 100*tkr)))
        else:
            v.append(("④ 信·体裁边界", "话题切换 %d 处（引号内 %.0f%%）——**叙述层**回避，仅提示" % (tkn, 100*tkr),
                      "引号内占比低 ⇒ 不在对白里，不触发降级"))
    if sc["gen_head"] > 0.05 or sc["gen_dup"] > 0.30:
        v.append(("生成物", "⚠ 疑似生成物/未清洗语料", "header %.1f%%，长句重复 %.1f%%" % (sc["gen_head"]*100, sc["gen_dup"]*100)))
    return v

# ---------------------------------------------------------------- 核字
def esc(c):
    return c.replace("\n", "⏎").replace("\t", "→").replace("\r", "⏎")

def neighbors(t, kw):
    prev, nxt = collections.Counter(), collections.Counter()
    for m in re.finditer(re.escape(kw), t):
        if m.start() > 0: prev[t[m.start()-1]] += 1
        if m.end() < len(t): nxt[t[m.end()]] += 1
    return prev, nxt

def disambiguate(t, kw):
    """#27 邻字检查：给出被某邻字吞并的比例，以及剔除该邻字后的计数"""
    prev, nxt = neighbors(t, kw)
    total = len(re.findall(re.escape(kw), t))
    if total == 0: return None
    res = {"total": total, "prev": [], "next": [], "alt": []}
    for c, n in prev.most_common(4):
        res["prev"].append((c, n, n/total))
        if n/total >= 0.30:
            alt = total - n
            res["alt"].append("剔「%s□」后 = %d（被吞 %.1f%%）" % (esc(c), alt, 100*n/total))
    for c, n in nxt.most_common(4):
        res["next"].append((c, n, n/total))
        if n/total >= 0.30:
            alt = total - n
            res["alt"].append("剔「□%s」后 = %d（被吞 %.1f%%）" % (esc(c), alt, 100*n/total))
    return res

# ---------------------------------------------------------------- #27 邻字分级
C_KW_NUM = "一二三四五六七八九十百千萬万两廿卅第"
C_KW_NUMWORD = re.compile(r"^(?:[一二三四五六七八九十百千萬]{1,3}|第[一二三四五六七八九十百千萬]{1,2})$")
C_ADJ_FREQ = 200            # 「末字＋后邻」二字组频次阈值

def _bgfreq(t, a, b):
    if a in ("^", "$") or b in ("^", "$"):
        return 0
    return t.count(a + b)

def classify_hits(t, kw, sample=3):
    """#27 升级（V26）：邻字命中不再「一刀切剔除」，改为分级。
    L1 数词借位（左）：前邻＋词首构成数词／前邻是序数标记 ⇒ 吞并（义变）
    L2 高频借字（右）：末字＋后邻构成的二字组频次 ≥ C_ADJ_FREQ ⇒ 吞并（义变）
    其余 ⇒ **待定**：机械判据不足以定性，附上下文样本交人工/后续。
    为什么必须分「吞并」与「搭配」：两者在裸计数里同形（都是「多出一个邻字」），
    但一个该删、一个该留；旧法把二者一起剔，**剔完仍然虚高**（实测见 库内地基 附五/附七）。
    """
    occ = list(re.finditer(re.escape(kw), t))
    if not occ:
        return None
    swallow, todo = [], []
    prev, nxt = collections.Counter(), collections.Counter()
    for m in occ:
        s, e = m.start(), m.end()
        p = t[s - 1] if s > 0 else "^"
        n = t[e] if e < len(t) else "$"
        prev[p] += 1; nxt[n] += 1
        l1 = bool(C_KW_NUMWORD.match(p + kw[0])) or p == "第"
        l2 = _bgfreq(t, kw[-1], n) >= C_ADJ_FREQ
        ctx = t[max(0, s - 12):min(len(t), e + 14)].replace("\n", "⏎")
        if l1 or l2:
            swallow.append((p, n, "L1" if l1 else "L2", ctx))
        else:
            todo.append((p, n, ctx))
    return {"total": len(occ), "swallow": swallow, "todo": todo,
            "prev": prev, "next": nxt, "todo_sample": todo[:sample]}

def cross_script(t, verdict_script, kw):
    """#20 跨字形：关键词在底本字形与另一字形各查一次"""
    if verdict_script == "繁體":
        conv = to_trad(kw); other = to_simp(kw)
    elif verdict_script == "简体":
        conv = to_simp(kw); other = to_trad(kw)
    else:
        conv, other = kw, (to_trad(kw) if kw == to_simp(kw) else to_simp(kw))
    a = len(re.findall(re.escape(kw), t))
    b = len(re.findall(re.escape(conv), t)) if conv != kw else a
    c = len(re.findall(re.escape(other), t)) if other != kw else 0
    flag = None
    if a == 0 and b > 0:
        flag = "⚠ 跨字形假阴性已拦截：原词 0 次，转「%s」后 %d 次" % (conv, b)
    return a, conv, b, other, c, flag

# ---------------------------------------------------------------- #27 通名侧
HAN1 = re.compile(r"[\u4e00-\u9fff]")
# 扩展宿主串时**不跨越虚词/连词**——否则会一路吃掉「乃至…」「如是…」这类套语
C_STOP = set("乃至亦所之其則即故以與及或若如是而于於為有著然")

# `--discover` 的虚词闸门（V28 实测加的）：并列切出来的 2–5 字候选里，含虚词/连词的
# 几乎都是**句子片段**（「故須破外」「此亦非真」「依於一異」…）。
# 空有二宗四个话题实测：候选 58 → 24（压掉 59%），而**真专名/名相一个不少**（18 → 18），
# 精确率 31% → **75%**。代价：`所` 若留在集里会误伤真名相「四無所畏」×139 ⇒ 已把 `所` 移出。
D_STOP = (set("乃至亦之其則即故以與及或若如是而于於為有著然")
          | set("云此何耶也矣乎哉須合實依作多故名時"))

def expand_host(t, kw, p, n, minfreq=3, maxlen=9):
    """#27 长吞并词（「隔字」的真相）：邻字画像只有*一字宽*，
    真正的吞并往往是*多字词*（「十|八不」的宿主是「十八不共法」）。
    从 p+kw+n 出发逐字扩展，只跨汉字、只在频次不跌时前进 ⇒ 报出宿主串。"""
    host = (p if HAN1.match(p or "") else "") + kw + (n if HAN1.match(n or "") else "")
    if not host or t.count(host) < minfreq:
        return None
    for _ in range(maxlen):
        if len(host) >= maxlen:
            break
        lc, rc = collections.Counter(), collections.Counter()
        for m in re.finditer(re.escape(host), t):
            s, e = m.start(), m.end()
            if s > 0 and HAN1.match(t[s - 1]):
                lc[t[s - 1]] += 1
            if e < len(t) and HAN1.match(t[e]):
                rc[t[e]] += 1
        bl = lc.most_common(1)[0] if lc else None
        br = rc.most_common(1)[0] if rc else None
        if bl and bl[0] in C_STOP: bl = None
        if br and br[0] in C_STOP: br = None
        cand = None
        if bl and (not br or bl[1] >= br[1]) and bl[1] >= minfreq:
            cand = bl[0] + host
        elif br and br[1] >= minfreq:
            cand = host + br[0]
        if not cand or t.count(cand) < minfreq:
            break
        host = cand
    return host, t.count(host)

def host_report(t, kw, top=3, minfreq=3):
    cc = classify_hits(t, kw, sample=0)
    if not cc:
        return []
    out, seen = [], set()
    cands = [(c, n) for c, n in cc["prev"].most_common(top)] + \
            [(c, n) for c, n in cc["next"].most_common(top)]
    for c, n in cands:
        if c in ("^", "$") or c in seen:
            continue
        seen.add(c)
        p = c if (c, n) in cc["prev"].most_common(top) else ""
        nn = "" if p else c
        r = expand_host(t, kw, p, nn, minfreq=minfreq)
        if r:
            out.append((r[0], r[1]))
    return sorted(set(out), key=lambda x: -x[1])[:top]

def count_union(t, terms):
    """并集计数：长词优先、左起贪心，避免子串重复计"""
    terms = sorted(set(x for x in terms if x), key=len, reverse=True)
    if not terms:
        return 0, {}
    per = {x: t.count(x) for x in terms}
    return len(re.compile("|".join(re.escape(x) for x in terms)).findall(t)), per

def dual_query(docs, generic, aliases):
    """#27 专名／通名双查：通名查不着专名 ⇒ 假阴性与「原文确实没有」同形。"""
    per = collections.Counter()
    gtot = utot = 0
    for d in docs:
        u, p = count_union(d["text"], [generic] + list(aliases))
        gtot += p.get(generic, 0)
        utot += u
        per.update(p)
    return gtot, utot, per

def _merge(ivs):
    ivs = sorted(ivs)
    out = []
    for s, e in ivs:
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out

def discover_names(t, kw, window=60, minlen=3, maxlen=5, top=12):
    """从「顿号并列结构」里自动发现专名候选——人不必先知道专名才查得到。
    **已标定（V28）**：加虚词闸门后空有二宗四话题精确率 **75%**（18/24，虚词集 D_STOP）；
    闸门前是 31%（18/58）——**压掉 59% 的噪声，真候选一个不少**。窗口重叠已按区间并集去重（否则集中度会 >100%）。"""
    ivs = _merge([[max(0, m.start() - window), min(len(t), m.end() + window)]
                  for m in re.finditer(re.escape(kw), t)])
    if not ivs:
        return []
    cnt = collections.Counter()
    for s0, e0 in ivs:
        for part in re.split(r"[、，。；：？！「」『』（）()《》〈〉\s\n\r]+", t[s0:e0]):
            part = part.strip()
            if minlen <= len(part) <= maxlen and all(HAN1.match(ch) for ch in part) and kw not in part \
               and not any(ch in D_STOP for ch in part):
                cnt[part] += 1
    return cnt.most_common(top)

# ---------------------------------------------------------------- 报告
def md_report(docs, kws, out_path=None, term_groups=None, discover=False, skipped=None, unsupported=None):
    L = []
    L.append("# 四通道批处理 · 报告")
    L.append("")
    L.append("> 生成时间：%s" % __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    _zh = [d for d in docs if d.get("lang") == "zh"]
    _en = [d for d in docs if d.get("lang") == "en"]
    _un = list(unsupported or [])
    L.append("> 语料：%d 份（中文 %d ／ 英文 %d%s），汉字合计 **%s**，英文词合计 **%s**" % (
        len(docs) + len(_un), len(_zh), len(_en),
        ("／不支持语种 %d" % len(_un)) if _un else "",
        format(sum(d["nhan"] for d in _zh), ","), format(sum(d["ntok"] for d in _en), ",")))
    if skipped:
        L.append("> ⚠ **%d 份被跳过**——**逐份打印实测单位数与真实理由**（V37 · A17：不再写死"
                 "「正文不足 50 单位」；静默跳过＝假阴性，与 #26 同族）：" % len(skipped))
        for x in skipped:
            L.append(">   - `%s`：unit=%s < 50 ｜ lang=%s（%s）" % (
                x["name"], format(x.get("unit", x.get("ntok", 0)), ","), x.get("lang", "?"),
                LANG_NAME.get(x.get("lang", "?"), "?")))
    if _un:
        L.append("> ⛔ **%d 份为不支持语种**——本工具只有中文／英文两张判据表，**不出读数**"
                 "（既不套中文表，也不落进英文表；与「跳过」分开记账）：" % len(_un))
        for x in _un:
            L.append(">   - `%s`：lang=%s（%s）｜ unit=%s ｜ 本工具无此语种判据表，不出读数" % (
                x["name"], x.get("lang", "?"), LANG_NAME.get(x.get("lang", "?"), "?"),
                format(x.get("unit", 0), ",")))
    L.append("> 工具版本：**V37**（十五条标定全部落地：A13 ① **编号冒充日期**（#50：`YYYY-MM` 的月份须合法且不在编号上下文里，并补 `C1_YEAR_RANGE` 收真年份区间——p5 三处编号全消失、真日期与真区间保住）；A14 ①② **取消闸门补全**（#51：中文词表补「废标／流标…」，新建英文取消词表 `C1_CANCEL_EN`，`fut_cancel_points` 改语种无关，`verdict_en` 改读可结算层并打印取消注记）；A15 ①④／#49① **位置闸门**（#48①④／#49①：行内引注年**不计时点、不作锚定**；落款行／版权页行／脚注尾注行不作锚定、不作时点；新增英文平行内核 `_fut_in_sent_en`，`fut_points_en` 同源调用）；A12 ① **已完成式时长**（#43 时长型「N 天内／N 个月内」后 12 字内见「了」⇒ 已完结＝回顾，非未来；实测删 2 处、逐条全为回顾式、0 真损失）；A11 ① **尾栏导航区**（#42「前一日／后一日 ·」行及其后标题行冒充本文时点，AIHOT 09-06／09-08 各漏 1 处 2028 年，全库 0 命中）；A10 ① **取消式计划**（#41 未来时点落在「取消／废弃」句中 ⇒ 文本层仍计、**可结算层降权**；AIHOT 09-30「取消原定下月发布」同形 3 处）；A9 ① **归档索引区**（#39 往期目录行／归档链接行冒充本文时点，成串≥3 连续才认）；A8 ① **回顾式相对时点**（#40 时长型「本季度／N个月内／窗口期」须前瞻同现）。A1 ① **刊头闸门**（#38 版面日期冒充本文时点）；A2 ① **量词型相对时点**（数周内／within a few weeks）；A3 ① **无年份月日**（截止 10 月 2 日）；A4 ① **引述框架扩族**（文件写明／报道同时披露／…披露：）；A5 ② **删《…》**（书名号≠引号）；A6 ② **新闻体引注形制**（据《X》报道／（注：／原文行）；A7 英文表**裸年份不吞中文**（修阴性对照泄漏）。承 V32：A-1 加 ① **体裁闸门**：日程语「将于＋具体时刻」只在**机构性文本**里计为可结算时点——全库 `将于`6／`今年N月`33 全在叙事体，裸补会重犯 #24 同形异域；A-2 加**语言路由＋英文平行判据表**：纯英文语料原被「不足 50 汉字」**静默跳过**，现按每千词单独判定、单独成表，禁止与中文合并。承 V31：E3 加**成文年告警**：frontmatter 的成文年与基准年相差 ≥3 年时标「① 判定作废，须 `--base-year` 重跑」——实测缠论原文 2006–2008 在基准年 2026 下未来时点 96→0；A′ ① 装「数据显示／统计显示／表明」；E1 `C4_TALK` 收紧 110→101 处，**不扩表**。承 V30：#34 同形异能、#33 同形异域、#32 来源面偏置；A17 **语种路由扩四档**（#52：ja／ko／cyr／other **显式拒绝**，跳过时打印实测单位数与真实理由，不套中文表、不落英文表；先判语种再判字形））")
    L.append("> 判读纪律：**判错通道 = 判出假结论**；四条通道分开报，不合并总分。")
    _nby = sum(1 for d in docs if d.get("fm_year") and abs(d["fm_year"] - C1_NOW_YEAR) >= 3)
    if _nby:
        L.append("> ⚠ **%d 份的成文年与本轮基准年 %d 相差 ≥3 年**——这些文件的 ① 判定无效，须用 `--base-year <成文年>` 重跑（E3 实测：缠论原文 2006–2008，未来时点 96 → 0）。" % (_nby, C1_NOW_YEAR))
    L.append("")
    L.append("## 一、总表")
    L.append("")
    L.append("| 文件 | 汉字／词 | 字形 | ①日期密度 | ②引注 | ③文体·定义/论辩 | ④禁止检验条款 | 生成物嫌疑 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for d in docs:
        sc = d["sc"]
        form = "定义" if sc["3_def"] > sc["3_arg"] else "论辩"
        reg = sc.get("3_reg", "?")
        gen = "⚠ %.0f%%/%.0f%%" % (sc["gen_head"]*100, sc["gen_dup"]*100) if (sc["gen_head"]>0.05 or sc["gen_dup"]>0.30) else "—"
        fbn = sc["4_forbid"]
        fbr = 0.0 if fbn == 0 else sc.get("4_forbid_q", 0) / fbn
        fbne = sc.get("4_forbid_bai", fbn) if sc.get("3_reg") == "白话体" else fbn
        f4s = "%d" % fbn if fbne == fbn else "%d→%d" % (fbn, fbne)
        if fbn >= 3 and fbr >= 0.60 and sc.get("3_reg") == "白话体":
            g4 = "↘降级(人物之口)"
        elif fbne >= 3 and sc["4_grade"] >= 3:
            g4 = "定罪级"
        elif fbne >= 3:
            g4 = "劝信级"
        elif sc.get("4_talk", 0) >= 3:
            g4 = "未命中(t%d)" % sc.get("4_talk", 0)
        else:
            g4 = "未命中"
        _ntv = "%s 词" % format(d["ntok"], ",") if d.get("lang") == "en" else format(d["nhan"], ",")
        L.append("| %s | %s | %s | %.2f | %.2f | %s (%.2f/%.2f) | %s（%s） | %s |" % (
            d["name"], _ntv, d["script"],
            sc["1_dens"], sc["2_cite"], "%s·%s" % (reg, form), sc["3_def"], sc["3_arg"],
            f4s, g4, gen))
    L.append("")
    L.append("## 二、逐份判定")
    L.append("")
    for d in docs:
        L.append("### %s" % d["name"])
        L.append("")
        L.append("- 路径：`%s`" % d["path"])
        _sz = "英文词 %s" % format(d["ntok"], ",") if d.get("lang") == "en" else "汉字 %s" % format(d["nhan"], ",")
        L.append("- 规模：%s ｜ 行 %s ｜ 编码 %s" % (_sz, format(d["lines"], ","), d["enc"]))
        if d.get("fm_year") and abs(d["fm_year"] - C1_NOW_YEAR) >= 3:
            L.append("  - ⚠ **成文年 %d ≠ 基准年 %d**（差 %d 年）⇒ 本份 ① 判定作废，须 `--base-year %d` 重跑"
                     % (d["fm_year"], C1_NOW_YEAR, abs(d["fm_year"] - C1_NOW_YEAR), d["fm_year"]))
        v, r, t_, s_, bad = d["scriptinfo"]
        L.append("- **字形（先锁字形 · #20）：%s**（繁专用 %s／简专用 %s，占比 %.3f）" % (v, format(t_, ","), format(s_, ","), r))
        if v == "混合" and bad:
            L.append("  - ⚠ 混用字样例：%s" % "、".join("%s×%d" % (c, n) for c, n in bad[:6]))
        for ch, res, ev in verdict(d["sc"]):
            L.append("- **%s**：%s ｜ %s" % (ch, res, ev))
        if d["sc"]["selfref"]:
            L.append("- 自指句式样本（③查自指辅助）：%s" % " / ".join(d["sc"]["selfref"]))
        L.append("")
    if kws:
        L.append("## 三、关键词核字（跨字形双查 + 邻字分级 · #20／#27）")
        L.append("")
        L.append("> 判读法：先看**裸计数**，再看**邻字分级**。邻字命中里混着两类**同形**的东西——")
        L.append("> **吞并**（邻字借走了词的字，词义已变，该删）与**搭配**（邻字只是挨着，词义未变，该留）——")
        L.append("> 所以**不能一刀切剔除**：剔完仍然虚高。本工具只把机器判得动的判为**确定吞并**")
        L.append(">（L1 数词借位／L2 高频借字），其余进**待定**栏并附样本，交人工定。")
        L.append("> 跨字形假阴性（原词 0 次、换字形才出现）单独列出，它证明「字面 0」≠「文本无」。")
        L.append("")
        L.append("| 关键词 | 裸计数 | 确定吞并 | 待定 | 待定占比 |")
        L.append("|---|---|---|---|---|")
        for kw in kws:
            tot = 0; nsw = 0; ntd = 0
            prev_all, next_all = collections.Counter(), collections.Counter()
            fn, todo_rows = [], []
            for d in docs:
                t = d["text"]
                a, conv, b, other, c_, flag = cross_script(t, d["script"], kw)
                tot += a
                if flag:
                    fn.append("%s：%s" % (d["name"], flag))
                cc = classify_hits(t, kw)
                if not cc:
                    continue
                nsw += len(cc["swallow"]); ntd += len(cc["todo"])
                prev_all += cc["prev"]; next_all += cc["next"]
                for p_, n_, ctx in cc["todo_sample"][:2]:
                    todo_rows.append((a, d["name"], p_, n_, ctx))
            L.append("| %s | %d | %d | %d | %.1f%% |" % (
                kw, tot, nsw, ntd, 0.0 if tot == 0 else 100 * ntd / tot))
            prof = []
            for tag, ctr in (("前", prev_all), ("后", next_all)):
                for c, n in ctr.most_common(3):
                    if tot and n / tot >= 0.05:
                        prof.append("%s「%s」%d（%.0f%%）" % (tag, esc(c), n, 100 * n / tot))
            if prof:
                L.append("  - 邻字画像：%s" % " · ".join(prof))
            hmax = {}
            for d in docs:
                for h, f in host_report(d["text"], kw):
                    if f > hmax.get(h, 0):
                        hmax[h] = f
            top_h = sorted(hmax.items(), key=lambda x: -x[1])[:3]
            if top_h:
                L.append("  - **宿主串（长吞并词）**：%s" % " · ".join(
                    "「%s」×%d" % (h, f) for h, f in top_h))
            for a, nm, p_, n_, ctx in sorted(todo_rows, key=lambda x: -x[0])[:6]:
                L.append("  - 待定 · %s（裸 %d）〔前「%s」后「%s」〕：…%s…" % (nm, a, esc(p_), esc(n_), ctx))
            for f in fn[:4]:
                L.append("  - ⚠ %s" % f)
            L.append("")
    if term_groups or discover:
        L.append("## 四、专名／通名双查（#27 的另一半）")
        L.append("")
        L.append("> **通名查不着专名**：文本用「迦毘羅／優樓迦／僧佉／衛世師」说事时，查「外道」近乎查空——")
        L.append("> **假阴性的样子与「原文确实没有」一模一样**。故通名与专名必须**各查一次**，不一致就不出结论。")
        L.append("")
        if term_groups:
            L.append("| 通名 | 通名计数 | 专名计数和 | 并集 | 通名覆盖率 | 判定 |")
            L.append("|---|---|---|---|---|---|")
            for g, al in term_groups:
                gtot, utot, per = dual_query(docs, g, al)
                atot = sum(per.get(x, 0) for x in al)
                cov = 0.0 if utot == 0 else 100.0 * gtot / utot
                vdct = "—"
                if utot and gtot / utot < 0.5:
                    vdct = "⚠ **通名严重漏检**（用专名说事）"
                elif gtot == 0 and utot:
                    vdct = "⚠ **通名 0 次、专名有**（通名假阴性）"
                L.append("| %s | %d | %d | %d | %.1f%% | %s |" % (g, gtot, atot, utot, cov, vdct))
            L.append("")
            for g, al in term_groups:
                _, _, per = dual_query(docs, g, al)
                det = "、".join("%s %d" % (k, per.get(k, 0)) for k in [g] + list(al))
                L.append("  - %s：%s" % (g, det))
            L.append("")
        if discover:
            L.append("**并列结构自动发现**（顿号并列里的 2–5 字名相候选，**已加虚词闸门**）——用于找「还不知道它叫什么的」专名：")
            L.append("")
            for kw in kws:
                cnt = collections.Counter()
                for d in docs:
                    cnt.update(dict(discover_names(d["text"], kw)))
                if cnt:
                    scored = []
                    for w, c in cnt.items():
                        tot_ = sum(d["text"].count(w) for d in docs)
                        if c < 2 or tot_ < 3:
                            continue
                        conc = c / tot_ if tot_ else 0.0
                        if conc >= 0.30:
                            scored.append((conc, c, w))
                    scored.sort(key=lambda x: (-x[0], -x[1]))
                    L.append("  - 「%s」邻域候选（**集中度 ≥30%%**）：%s" % (
                        kw, "、".join("%s ×%d（集中度 %.0f%%）" % (w, c, 100 * s)
                                      for s, c, w in scored[:10]) or "—"))
            L.append("")
    txt = "\n".join(L)
    if out_path:
        io.open(out_path, "w", encoding="utf-8", newline="\n").write(txt)
    return txt

# ---------------------------------------------------------------- main
def main():
    global C1_NOW_YEAR
    C1_BASE_DEFAULT = C1_NOW_YEAR
    ap = argparse.ArgumentParser(description="四通道批处理")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", dest="jsonout", default=None)
    ap.add_argument("--kw", nargs="*", default=[])
    ap.add_argument("--terms", default=None,
                    help="词表文件：每行「通名|专名1,专名2,...」或单独一个词（走专名／通名双查）")
    ap.add_argument("--discover", action="store_true", help="从并列结构自动发现专名候选")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--base-year", type=int, default=None, help="① 的语料基准年（默认 %d）" % C1_NOW_YEAR)
    ap.add_argument("--keep-frontmatter", dest="keep_fm", action="store_true",
                    help="保留 YAML frontmatter（默认剥离：#29 元数据冒充内容）")
    a = ap.parse_args()

    files = collect(a.paths)
    if not files:
        print("没有找到可读文件"); return 1
    docs = []
    for f in files:
        try:
            t, enc = read_text(f)
            _fy = fm_year(t)
            if not a.keep_fm:
                t = strip_frontmatter(t)
            docs.append({"path": f, "name": os.path.basename(f), "text": t, "enc": enc,
                         "nhan": han_count(t), "lines": t.count("\n") + 1, "fm_year": _fy})
        except Exception as e:
            print("跳过", f, str(e)[:60])
    for d in docs:
        d["nhan"] = han_count(d["text"])
        d["lang"] = lang_of(d["text"])
        d["ntok"] = word_count(d["text"]) if d["lang"] == "en" else d["nhan"]
        d["unit"] = lang_unit(d["text"], d["lang"])   # V37 · A17（#52）：实测单位数（非写死）
    _unsupported = [d for d in docs if d["lang"] not in SUPPORTED_LANGS]
    _scorable = [d for d in docs if d["lang"] in SUPPORTED_LANGS]
    _skipped = [d for d in _scorable if d["unit"] < 50]
    for d in _skipped:
        d["reason"] = "正文不足 50 单位"
    docs = [d for d in _scorable if d["unit"] >= 50]
    for d in docs:
        if d["lang"] == "en":
            d["script"] = "英文"; d["scriptinfo"] = ("英文", 0.0, 0, 0, [])
            d["sc"] = channel_scores_en(d["text"])
        else:
            v, r, t_, s_, bad = script_of(d["text"], d["lang"])   # V37 · A17：先判语种，再判字形
            d["script"] = v; d["scriptinfo"] = (v, r, t_, s_, bad)
            d["sc"] = channel_scores(d["text"])
    kws = list(a.kw)
    term_groups = []
    if a.terms and os.path.exists(a.terms):
        for line in io.open(a.terms, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split("|")
            g = parts[0].strip()
            al = [x.strip() for x in parts[1].split(",") if x.strip()] if len(parts) > 1 else []
            if g:
                kws.append(g)
                if al:
                    term_groups.append((g, al))
    rep = md_report(docs, kws, a.out, term_groups=term_groups, discover=a.discover, skipped=_skipped, unsupported=_unsupported)
    if a.jsonout:
        io.open(a.jsonout, "w", encoding="utf-8", newline="\n").write(
            json.dumps({"docs": [{"name": d["name"], "path": d["path"], "nhan": d["nhan"], "ntok": d["ntok"], "unit": d.get("unit"),
                                  "lang": d.get("lang"), "script": d["script"], "script_ratio": d["scriptinfo"][1],
                                  "fm_year": d.get("fm_year"),
                                  "scores": {k: v for k, v in d["sc"].items() if not isinstance(v, list)},
                                  "selfref": d["sc"]["selfref"]} for d in docs]},
                       ensure_ascii=False, indent=1))
    if not a.quiet:
        sys.stdout.write(rep)
    return 0

if __name__ == "__main__":
    sys.exit(main())