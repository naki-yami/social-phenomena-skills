# -*- coding: utf-8 -*-
"""分析看板生成器 · social-analysis

把一次分析的「结论数据」渲染成一页直观看板（单文件 HTML，无外部依赖、无联网、无 JS）。

用法：
    python build_report_html.py 报告数据.json                 # 输出同目录同名 .html
    python build_report_html.py 报告数据.json -o 输出.html

输入契约见 references/可视化规范.md。
规矩：本页只画「已经判出来的」，不新增判据；明细与数字留在报告文本里，本页只留结构。
"""
import io, json, sys, os, html

CIRC = 314.159  # 2*pi*50


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


# ---------------------------------------------------------------- 组件

def ring(ratio, color, label=""):
    r = max(0.0, min(1.0, float(ratio)))
    arc = CIRC * r
    txt = ("" if label == "" else
           '<text x="60" y="61" text-anchor="middle" dominant-baseline="central" '
           'font-size="31" font-family="Georgia,serif" fill="var(--fg)">%s</text>' % esc(label))
    return (
        '<svg viewBox="0 0 120 120" class="ring" role="img">'
        '<circle cx="60" cy="60" r="50" fill="none" stroke="var(--track)" stroke-width="11"/>'
        '<circle cx="60" cy="60" r="50" fill="none" stroke="%s" stroke-width="11" '
        'stroke-dasharray="%.2f %.2f" transform="rotate(-90 60 60)"/>%s'
        "</svg>" % (color, arc, CIRC - arc, txt)
    )


LV = {"ok": "var(--ok)", "mid": "var(--mid)", "no": "var(--no)"}


def sec_hero(d):
    v = get(d, "结论", default={}) or {}
    conf = v.get("置信度", "")
    cells = [
        ("置信度", conf), ("应验窗口", v.get("窗口", "")),
        ("双轴定位", v.get("双轴", "")), ("行动", v.get("行动", "")),
    ]
    kpi = "".join(
        '<div><div class="k">%s</div><div class="v">%s</div></div>' % (esc(k), esc(x))
        for k, x in cells if x
    )
    return """
<section class="hero">
  <p class="assert">%s</p>
  <div class="kpis">%s</div>
</section>""" % (esc(v.get("断言", "")), kpi)


def sec_chain(items):
    if not items:
        return ""
    rows = []
    for i, it in enumerate(items):
        chips = []
        if it.get("得"):
            chips.append('<span class="chip got"><i>得到</i>%s</span>' % esc(it["得"]))
        if it.get("砍"):
            chips.append('<span class="chip cut"><i>砍掉</i>%s</span>' % esc(it["砍"]))
        rows.append(
            '<li><div class="node"><span class="dot">%s</span></div>'
            '<div class="card"><h4>%s</h4><p class="ask">%s</p>'
            '<div class="chips">%s</div></div></li>'
            % (esc(it.get("n", "%02d" % i)), esc(it.get("站", "")),
               esc(it.get("问", "")), "".join(chips))
        )
    return """
<section>
  <h2><span class="idx">A</span>分析路径</h2>
  <p class="sub">一步不跳，也一步不回头。每站只写三件事：问什么、得到什么、砍掉什么。</p>
  <ol class="chain">%s</ol>
</section>""" % "".join(rows)


def sec_sources(s):
    if not s:
        return ""
    present = s.get("在场", []) or []
    absent = s.get("缺席", []) or []
    rows = []
    for x in present:
        rows.append('<div class="srow"><div class="snm">%s</div><div class="sct">%s 份</div>'
                    '<div class="snt">%s</div></div>'
                    % (esc(x.get("名", "")), esc(x.get("数", "")), esc(x.get("注", ""))))
    for x in absent:
        rows.append('<div class="srow absent"><div class="snm">%s</div><div class="sct">0 份</div>'
                    '<div class="snt">%s</div></div>'
                    % (esc(x.get("名", "")), esc(x.get("注", ""))))
    return """
<section>
  <h2><span class="idx">B</span>来源面</h2>
  <p class="sub">%s</p>
  <div class="halfbar">
    <div class="half in"><div class="fill"></div><b>本机出口可达</b><span>%s 份 · %s</span></div>
    <div class="half out"><div class="fill"></div><b>不可达 · 缺席</b><span>%s</span></div>
  </div>
  <div class="srcbox">%s</div>
</section>""" % (
        esc(s.get("说明", "")),
        sum(int(x.get("数", 0) or 0) for x in present),
        esc(s.get("在场名", "大陆侧")),
        esc(s.get("缺席名", "")),
        "".join(rows),
    )


def sec_channels(chs):
    if not chs:
        return ""
    cards = []
    for c in chs:
        quarters = ""
        if c.get("四分"):
            bars = "".join(
                '<div class="qbar"><span class="qv">%s</span><span class="qk">%s</span></div>'
                % (esc(q.get("v", "")), esc(q.get("k", ""))) for q in c["四分"]
            )
            quarters = '<div class="quarters">%s</div>' % bars
        cards.append(
            '<div class="chan">%s'
            '<div class="cbody"><div class="chead"><b>%s %s</b><span class="badge %s">%s</span></div>'
            '<p>%s</p>%s</div></div>'
            % (ring(c.get("值", 0) or 0, LV.get(c.get("级", "no"), "var(--no)"), c.get("数", "")),
               esc(c.get("号", "")), esc(c.get("名", "")),
               esc(c.get("级", "no")), esc(c.get("档", "")),
               esc(c.get("话", "")), quarters)
        )
    return """
<section>
  <h2><span class="idx">C</span>四把尺子</h2>
  <p class="sub">分开量，不合并总分——合并等于把四种错混成一种。</p>
  <div class="chans">%s</div>
</section>""" % "".join(cards)


def sec_structure(items):
    if not items:
        return ""
    cells = []
    for it in items:
        miss = it.get("缺")
        cls = " cell miss" if miss and not it.get("答") else " cell"
        body = ('<p class="a">%s</p>' % esc(it["答"])) if it.get("答") else '<p class="noans">答不上</p>'
        cells.append('<div class="%s"><div class="q">%s</div>%s%s</div>'
                     % (cls.strip(), esc(it.get("问", "")), body,
                        ('<p class="gap">%s</p>' % esc(miss)) if miss else ""))
    return """
<section>
  <h2><span class="idx">D</span>结构四问</h2>
  <p class="sub">判不了的部分必须写出来——漏了「缓冲」，就是把「还撑得住」当成「没问题」。</p>
  <div class="grid2">%s</div>
</section>""" % "".join(cells)


def sec_axes(a):
    if not a:
        return ""
    cells = []
    for i, x in enumerate(a.get("格", []) or []):
        cls = "cell here" if str(x.get("k")) == str(a.get("位置")) else "cell"
        cells.append('<div class="%s"><b>%s</b><span>%s</span></div>'
                     % (cls, esc(x.get("t", "")), esc(x.get("s", ""))))
    return """
<section>
  <h2><span class="idx">E</span>双轴定位</h2>
  <p class="sub">两轴分开记，禁止加权。高亮的那一格是本次的位置。</p>
  <div class="grid2 axes">%s</div>
</section>""" % "".join(cells)


def sec_domains(items):
    if not items:
        return ""
    chips = []
    for x in items:
        st = x.get("态", "")
        cls = "dom in" if st == "在场" else ("dom out" if st in ("拒答", "缺席") else "dom na")
        chips.append('<div class="%s"><b>%s</b><span class="st">%s</span><span class="nt">%s</span></div>'
                     % (cls, esc(x.get("域", "")), esc(st), esc(x.get("注", ""))))
    return """
<section>
  <h2><span class="idx">F</span>六域路由</h2>
  <p class="sub">谁有资格判。不做这层分流，六域会争相认领同一处证据。</p>
  <div class="doms">%s</div>
</section>""" % "".join(chips)


def sec_falsify(items):
    if not items:
        return ""
    lis = "".join("<li>%s</li>" % esc(x) for x in items)
    return """
<section>
  <h2><span class="idx">G</span>反证条件</h2>
  <p class="sub">出现任一条，上面的判断就算错。写不出这一栏的，不是分析，是感想。</p>
  <ul class="falsify">%s</ul>
</section>""" % lis


def sec_limits(items, note):
    if not items and not note:
        return ""
    lis = "".join("<li>%s</li>" % esc(x) for x in (items or []))
    n = ('<p class="limnote">%s</p>' % esc(note)) if note else ""
    return """
<section>
  <h2><span class="idx">H</span>边界</h2>
  <ul class="limits">%s</ul>%s
</section>""" % (lis, n)


# ---------------------------------------------------------------- 模板

CSS = """
:root{
  --bg:#FAF8F5; --surface:#FFFFFF; --fg:#1A1917; --muted:#6B655E; --faint:#9A938A;
  --border:#E4DED6; --rule:#CFC7BC; --accent:#A8342A; --track:#EDE7DF;
  --ok:#2F6B4F; --mid:#C2A24A; --no:#C9C1B6;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  --serif:"Songti SC","Source Han Serif SC","Noto Serif CJK SC","SimSun",Georgia,serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:15px;line-height:1.62}
.wrap{max-width:1080px;margin:0 auto;padding:48px 28px 80px}
h1,h2,h4{font-family:var(--serif);font-weight:600;margin:0}
h1{font-size:15px;letter-spacing:.06em;color:var(--muted);font-weight:400;font-family:var(--sans)}
h2{font-size:21px;display:flex;align-items:baseline;gap:10px;margin-bottom:4px}
h2 .idx{font-family:var(--serif);font-size:13px;color:#fff;background:var(--fg);width:22px;height:22px;
        line-height:22px;text-align:center;border-radius:50%;flex:0 0 auto;align-self:center}
.lede{font-family:var(--serif);font-size:44px;line-height:1.2;margin:10px 0 8px;letter-spacing:.01em}
.hsub{margin:0;color:var(--muted);font-size:14px;max-width:62ch}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 18px}
section{padding:34px 0 6px;border-top:1px solid var(--border);margin-top:30px}
section:first-of-type{border-top:0;margin-top:0}
.hero{border:1px solid var(--border);border-left:4px solid var(--accent);background:var(--surface);
      padding:22px 24px;margin-top:22px}
.assert{font-family:var(--serif);font-size:22px;line-height:1.5;margin:0 0 18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--border);
      border:1px solid var(--border)}
.kpis>div{background:var(--surface);padding:10px 13px}
.kpis .k{font-size:11.5px;color:var(--faint);letter-spacing:.04em}
.kpis .v{font-family:var(--serif);font-size:16px;margin-top:2px}
ol.chain{list-style:none;margin:0;padding:0;position:relative}
ol.chain:before{content:"";position:absolute;left:15px;top:10px;bottom:26px;width:1px;background:var(--rule)}
ol.chain li{display:grid;grid-template-columns:31px 1fr;gap:14px;padding:0 0 14px}
ol.chain .dot{position:relative;z-index:1;display:block;width:31px;height:31px;border-radius:50%;
  background:var(--fg);color:#fff;font-family:var(--serif);font-size:12.5px;line-height:31px;text-align:center}
ol.chain .card{background:var(--surface);border:1px solid var(--border);padding:11px 15px}
ol.chain h4{font-size:16px;margin-bottom:3px}
ol.chain .ask{margin:0;font-size:13.5px;color:var(--muted)}
.chips{margin-top:8px;display:flex;flex-wrap:wrap;gap:7px}
.chip{font-size:12.5px;padding:3px 9px;border:1px solid var(--border);background:#FCFAF7}
.chip i{font-style:normal;color:var(--faint);margin-right:5px;font-size:11px}
.chip.got{border-color:#BFD8C9;background:#F2F8F4}
.chip.cut{border-color:#E4CDC7;background:#FCF5F3}
.halfbar{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);border:1px solid var(--border);margin-bottom:14px}
.half{background:var(--surface);padding:12px 14px 14px}
.half .fill{height:16px;margin-bottom:9px}
.half.in .fill{background:#3E6B52}
.half.out .fill{background:repeating-linear-gradient(135deg,#FBF7F3,#FBF7F3 6px,#F1E9E0 6px,#F1E9E0 12px);border:1px solid var(--border)}
.half b{font-family:var(--serif);font-size:15px;display:block}
.half.in b{color:#3E6B52}
.half.out b{color:var(--accent)}
.half span{font-size:12.5px;color:var(--muted)}
.srcbox{border:1px solid var(--border);background:var(--surface)}
.srow{display:grid;grid-template-columns:200px 80px 1fr;gap:12px;padding:9px 14px;
      border-bottom:1px solid var(--border);align-items:baseline;font-size:13.5px}
.srow:last-child{border-bottom:0}
.srow .sct{font-family:var(--serif)}
.srow .snt{color:var(--muted);font-size:12.5px}
.srow.absent{background:repeating-linear-gradient(135deg,#FBF7F3,#FBF7F3 7px,#F1E9E0 7px,#F1E9E0 14px)}
.srow.absent .snm,.srow.absent .sct{color:var(--accent)}
.chans{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.chan{display:grid;grid-template-columns:104px 1fr;gap:14px;align-items:center;
      border:1px solid var(--border);background:var(--surface);padding:14px 16px}
.ring{width:104px;height:104px}
.chead{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.chead b{font-family:var(--serif);font-size:16px}
.badge{font-size:12px;padding:2px 9px;border:1px solid var(--border)}
.badge.ok{color:var(--ok);border-color:#BFD8C9;background:#F2F8F4}
.badge.mid{color:#8A6D1F;border-color:#E3D6AE;background:#FBF7EA}
.badge.no{color:var(--muted);border-color:var(--border);background:#F7F4F0}
.cbody p{margin:6px 0 0;font-size:13px;color:#403C36}
.quarters{display:flex;gap:14px;margin-top:8px;flex-wrap:wrap}
.qbar{font-size:12px;color:var(--muted)}
.qbar .qv{font-family:var(--serif);font-size:15px;color:var(--fg);margin-right:5px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);border:1px solid var(--border)}
.grid2 .cell{background:var(--surface);padding:13px 16px}
.grid2 .cell .q{font-family:var(--serif);font-size:15px;margin-bottom:4px}
.grid2 .cell .a{margin:0;font-size:13.5px}
.grid2 .cell .gap,.grid2 .cell .noans{margin:6px 0 0;font-size:12.5px;color:var(--muted)}
.grid2 .cell.miss{background:repeating-linear-gradient(135deg,#FBF7F3,#FBF7F3 7px,#F1E9E0 7px,#F1E9E0 14px)}
.grid2 .cell.miss .noans{color:var(--accent);font-family:var(--serif);font-size:15px}
.grid2.axes .cell{display:flex;flex-direction:column;gap:3px}
.grid2.axes .cell b{font-family:var(--serif);font-size:15px;font-weight:600}
.grid2.axes .cell span{font-size:12.5px;color:var(--muted)}
.grid2.axes .cell.here{background:#F4EFE8;box-shadow:inset 3px 0 0 var(--accent)}
.grid2.axes .cell.here b{color:var(--accent)}
.doms{display:grid;grid-template-columns:repeat(auto-fit,minmax(288px,1fr));gap:1px;background:var(--border);
      border:1px solid var(--border)}
.dom{background:var(--surface);padding:12px 14px;display:flex;flex-direction:column;gap:2px}
.dom b{font-family:var(--serif);font-size:15px}
.dom .st{font-size:12px}
.dom .nt{font-size:12px;color:var(--muted)}
.dom.in .st{color:var(--ok)} .dom.in{box-shadow:inset 3px 0 0 var(--ok)}
.dom.out{background:repeating-linear-gradient(135deg,#FBF7F3,#FBF7F3 7px,#F1E9E0 7px,#F1E9E0 14px)}
.dom.out .st{color:var(--accent)} .dom.out b{color:var(--muted)}
.dom.na .st{color:var(--faint)}
ul.falsify{list-style:none;margin:0;padding:0;display:grid;gap:10px}
ul.falsify li{border:1px solid var(--border);border-left:3px solid var(--accent);background:var(--surface);
  padding:11px 15px;font-size:14px}
ul.falsify li:before{content:"若 ";font-family:var(--serif);color:var(--accent)}
ul.limits{margin:0;padding-left:20px;color:var(--muted);font-size:13.5px}
.limnote{margin-top:12px;font-size:12.5px;color:var(--faint)}
footer{margin-top:56px;padding-top:18px;border-top:2px solid var(--fg);font-size:12.5px;color:var(--muted);
  display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}
@media (max-width:780px){
  .wrap{padding:28px 16px 60px}
  .lede{font-size:27px}
  .chans,.grid2{grid-template-columns:1fr}
  .srow{grid-template-columns:1fr;gap:2px}
  .chan{grid-template-columns:78px 1fr}
  .ring{width:78px;height:78px}
}
@media print{body{background:#fff}section{break-inside:avoid}}
"""


def build(d):
    parts = [
        sec_hero(d),
        sec_chain(d.get("思维链")),
        sec_sources(d.get("来源面")),
        sec_channels(d.get("四通道")),
        sec_structure(d.get("结构四问")),
        sec_axes(d.get("双轴")),
        sec_domains(d.get("六域")),
        sec_falsify(d.get("反证")),
        sec_limits(d.get("边界"), d.get("边界注")),
    ]
    head = """
<header>
  <h1>分析看板 · %s</h1>
  <div class="lede">%s</div>
  <p class="hsub">%s</p>
</header>""" % (esc(d.get("日期", "")), esc(d.get("题目", "")), esc(d.get("副题", "")))
    foot = """
<footer><span>%s</span><span>%s</span></footer>""" % (esc(d.get("脚注", "")), esc(d.get("日期", "")))
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>%s · 分析看板</title>\n<style>%s</style>\n</head>\n<body>\n<div class=\"wrap\">%s\n%s\n%s\n</div>\n</body>\n</html>\n"
        % (esc(d.get("题目", "")), CSS, head, "".join(parts), foot)
    )


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("用法: build_report_html.py 报告数据.json [-o 输出.html]\n")
        return 2
    src = sys.argv[1]
    out = None
    if "-o" in sys.argv:
        out = sys.argv[sys.argv.index("-o") + 1]
    else:
        out = os.path.splitext(src)[0] + ".html"
    d = json.loads(io.open(src, encoding="utf-8").read())
    h = build(d)
    io.open(out, "w", encoding="utf-8", newline="\n").write(h)
    sys.stdout.write("written %s (%d chars)\n" % (out, len(h)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
