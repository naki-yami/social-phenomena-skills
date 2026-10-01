# social-phenomena-skills

两个 **Codex skill**：把「分析一个社会现象」这件事，拆成**可复现的读数**和**能被推翻的判断卡**。

> 这是方法本身（判据 ＋ 工具 ＋ 流程）。标定这些判据所用的原始语料统计与逐条台账，
> 在另一个仓库 `social-phenomena-analysis` 的 `outputs/` 下（**目前未公开**）。
> **跑数不依赖那份账本**——本仓库自包含。

## 两个 skill 的分工

| skill | 管什么 | 一句话 |
|---|---|---|
| **`social-analysis`** | 判据与流程 | 说「分析 X」，就走全流程：联网找料 → 四通道读数 → 自指 → 结构四问 → 六域路由 → 判断卡 → 三件产物 |
| **`sitongdao`** | 机器读数 | 四条检验通道的批量执行器：**纯标准库、离线、不装包** |

**判据与读数分家**：阈值是判据的一部分（住在 skill 里），**语料统计结果只住账本**（skill 不复制任何读数）。

## 装

把两个目录拷进 skills 根目录即可：

- Codex：`~/.agents/skills/` 或 `$CODEX_HOME/skills/`
- 拷完目录结构应是 `…/skills/social-analysis/SKILL.md`、`…/skills/sitongdao/SKILL.md`

```
cp -r social-analysis sitongdao ~/.agents/skills/
```

两处实现约束：

- `sitongdao/scripts/简繁字典.tsv` **必须与脚本同目录**（脚本按自身路径找它）。取自 OpenCC，Apache-2.0。
- `social-analysis` 出**结构图**要另装第三方 skill **[archify](https://github.com/tt-a1i/archify)**（MIT，不随本仓库分发）：

  ```
  node <archify>/bin/archify.mjs finalize architecture <candidate.json> <out.html> --quality showcase --json
  ```

## 跑一遍

```
python sitongdao/scripts/四通道批处理.py <语料目录> --base-year 2026 --out 读数.md --json 读数.json
```

读法、什么时候**不许**跑、被跳过的文件怎么逐份看——见 `sitongdao/SKILL.md`。

## 它是什么

- **方法论**：存在论 → 现象学 → 认识论 → 方法论 → 实践论五部，回答「分析者站在哪里、对象怎么被造出来、怎么知道、怎么分析、落回哪里」。
- **尺子**：四条检验通道——**① 命中**（本文自己的未来时点）／**② 文献**（出处）／**③ 自洽**（论证）／**④ 信**（文本自带禁止检验的条款）。**四条分开报，不合并总分。**
- **账本**：每条判据都对着真实语料标定过，**标定失败的记录也留着**（不装的判据和装上的判据一样记账）。

分析对象**不限于新闻**：哲学、政治、经济、金融、历史、AI、佛学都真跑过。

## 已知边界（写在 skill 里，不是遗漏）

- **③ 的英文侧未标定**——只报数，不判读。
- **第四语种**（日／韩／西里尔）会被**显式拒绝**，不套中文表也不落进英文表。
- **#49② 伪将来时**只装了 ①，② 记账未装（阈值不够）。
- **换语料就要重标定**：在 A 语料上标出的精确率，不得搬到 B 语料。

## 许可

**MIT**（见 [`LICENSE`](LICENSE)）——可自由使用、修改、分发、商用，保留版权与许可声明即可。

例外与归属：

- `sitongdao/scripts/简繁字典.tsv` 取自 [OpenCC](https://github.com/BYVoid/OpenCC)，**Apache-2.0**——随文件保留其归属；该文件本身仍按 Apache-2.0（与 MIT 兼容）。
- **标定账本不在本仓库**（见上文），因此不在本许可范围内。
- 出结构图用的 **archify** 是第三方作品（上游 [tt-a1i/archify](https://github.com/tt-a1i/archify)，MIT），**不随本仓库分发**。
