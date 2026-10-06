# manual-baking

**把在线文档手册「烘焙」成本地可检索知识库的方法论** —— 一套可复用的流程与踩坑清单，不是某个厂商的语料。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Type](https://img.shields.io/badge/type-AI%20Skill-8A2BE2.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-3776AB.svg)

> 本仓库是 **WorkBuddy / CodeBuddy 技能**，装到 `~/.workbuddy-ai/skills/manual-baking/`
> （另一套客户端对应 `~/.workbuddy/skills/`）即生效，无需注册。
> 面向人的说明看本文件，面向 AI 的执行细则看 [`SKILL.md`](./SKILL.md)。

---

## 它解决什么问题

厂商文档站（金蝶、泛微、纷享销客、各类 SaaS 的开发者手册）通常**只有在线版**：
搜索弱、不能离线、翻页慢，写代码时来回切换极其低效。

这个技能把「抓取 → 转换 → 索引 → 检索 → 自检」整条链路固化下来，
并且**要求用脚本证明没有丢内容**——而不是"看起来抓完了"。

产出是一个自带全文检索的本地知识库，通常还会顺带做成一个独立的 skill 仓库
（如 [`weaver-e9-dev`](https://github.com/silent-reader-cn/weaver-e9-dev)、
[`kingdee-cosmic-dev`](https://github.com/silent-reader-cn/kingdee-cosmic-dev)、
[`fxiaoke-dev`](https://github.com/silent-reader-cn/fxiaoke-dev)）。

---

## 八步流程

| 步骤 | 要点 | 对应工具 | 章节 |
| :--- | :--- | :--- | :--- |
| **0 定结构** | 按「块」组织 `references/`，Markdown 是唯一真相源 | `knowledge.json` | [§0](./SKILL.md#0-先定结构) |
| **1 侦察** | **先判别 SPA 还是 SSG** —— 决定是挖 JSON 接口还是直接解析 HTML | `tools/siteprobe.py` | [§1](./SKILL.md#1-侦察找到-spa-背后的-json-接口) |
| **2 抓取** | 原始响应存档、增量、重试限速、清理遗留 | `tools/fetch_manual.py` | [§2](./SKILL.md#2-抓取脚本的必备设计) |
| **3 转换** | 零依赖自己写 HTML→Markdown，分清"富文本"与"结构化导出包" | `kbkit/html2md.py` | [§3](./SKILL.md#3-html--markdown自己写别指望库) |
| **4 检索** | 分层加权排序、按命中位置截断、**path 必须参与检索** | `scripts/search.py` | [§4](./SKILL.md#4-统一检索脚本scriptsearchpy) |
| **5 索引** | 从 Markdown 派生 `_INDEX.md`，绝不手工维护 | `scripts/build_index.py` | [§5](./SKILL.md#5-索引脚本scriptbuild_indexpy) |
| **6 自检** | 11 项完整性检查，证明没丢内容、检索不会静默截断 | `tools/selftest.py` | [§6](./SKILL.md#6-自检脚本toolsselftestpy--这一步不能省) |
| **7 收尾** | README/SKILL 写清用法与上游数据特征 | `templates/` | [§7](./SKILL.md#7-收尾) |
| **8 实测** | **有凭据就一定拿真实环境跑一遍**，把文档与线上不符之处回写 | — | [§8](./SKILL.md#8-有凭据就实测--文档与线上几乎总有出入) |

---

## 自带工具包 `kbkit/`

流程不止是文档 —— 本仓库带一套**可直接跑**的零依赖工具包。原本这些脚本在
`weaver-e9-dev` / `kingdee-cosmic-dev` / `fxiaoke-dev` 里各有一份**已经分叉的副本**
（同样的架构、同样的 bug 修了三四遍，站点差异却硬编码在代码里），现在收敛成一套
**配置驱动**的实现：**站点相关的东西全进 `knowledge.json`，代码不再改**。

```
kbkit/
├── config.py       配置模型（块/别名/排序权重/HTML 选择器）
├── corpus.py       语料遍历 + frontmatter 解析（所有模块共用，避免漂移）
├── html2md.py      通用 HTML → Markdown
├── search.py       分层加权检索（path 参与打分）
├── build_index.py  从 Markdown 派生各级 _INDEX.md
├── selftest.py     11 项完整性自检
├── probe.py        站点侦察：判 SPA/SSG、从 app.js 提取路由与中文层级
└── fetch.py        并发抓取 → 转换 → 串行定路径写盘
```

### 一条命令起一个新知识库

```bash
python tools/kb_new.py ../my-kb \
  --name my-kb --title "某某文档知识库" --source https://docs.example.com/ \
  --blocks "/start/:01_start:快速开始:start,/api/:02_api:接口手册:api" \
  --git
```

工具包会 **vendoring** 进新仓库（保持每个 skill 自包含，不跨仓库 import），
并渲染 `knowledge.json` / `SKILL.md` / `README.md` / `.gitignore` / `LICENSE`。
之后按提示走六步即可：

```bash
python tools/siteprobe.py detect <首页URL>     # 先判站点类型，别猜
python tools/siteprobe.py routes <首页URL> --out _source/_toc.json --blocks "..."
python tools/fetch_manual.py                   # 抓取
python scripts/build_index.py                  # 建索引
python tools/selftest.py --online 30           # 自检
```

### 实测验证

用 `kbkit` 的通用脚本跑 `fxiaoke-dev` 的 **1,307 篇真实语料**：

| 项 | 结果 |
| :--- | :--- |
| 索引文件数 | 255（与原专用脚本一致） |
| 索引路径 + 数据逐条比对 | **254/255 完全一致**（唯一差异是顶层索引的链接显示文字） |
| 检索结果 | 与原专用脚本逐条一致 |
| 自检 | 11 项全过（含联网抽样 A2 文本保真 20/20、A3 结构守恒 0 不一致） |

---

## 三个最容易踩空的地方

这套流程的价值有一半在"知道哪里会翻车"。挑三条最贵的：

**① 站点类型判错，整条路白走。**
现代文档站不都是 SPA。VuePress / Plume 这类 **SSG 站点正文就在 HTML 里**，
`curl` 一次就能拿全，不需要挖接口。反过来，Nuxt/Next 的客户端渲染站
`curl` 只能拿到标题骨架。判据：`grep -c 'vp-doc' page.html` 有值 → SSG。
（[§1.5](./SKILL.md#15-反例vuepress-2--plume-这类-ssg-站点)）

**② 自检报错时，先怀疑检查器。**
实测五轮报错里四轮是检查器自己的 bug：两侧文本归一化不对称、
表格分隔行按子串计数、正则 `[^)]` 切断带括号的文件名、
探针抽到了跨页样板句。**"先怀疑检查器，再怀疑数据"** 是省时间的铁律。
（[§6](./SKILL.md#-先怀疑检查器再怀疑数据)）

**③ 文档 ≠ 线上。**
某次对接实测挖出 8 处偏差：文档没写的硬约束、错误码表含义全错、
字段命名两套风格、静默部分失败（返回成功却没生效）、
手册实体清单与租户实际只重合 1/4。**拿到凭据就实测，并把差异回写。**
（[§8](./SKILL.md#8-有凭据就实测--文档与线上几乎总有出入)）

---

## 参考实现

流程本身是厂商无关的，可直接对照这三个真实仓库的源码：

| 仓库 | 站点类型 | 可看什么 |
| :--- | :--- | :--- |
| [`weaver-e9-dev`](https://github.com/silent-reader-cn/weaver-e9-dev) | 多来源合并 | 四块结构、检索脚本 |
| [`kingdee-cosmic-dev`](https://github.com/silent-reader-cn/kingdee-cosmic-dev) | 结构化导出包 | `fetch_manual.py` / `html2md.py` / `selftest.py` 完整范例 |
| [`fxiaoke-dev`](https://github.com/silent-reader-cn/fxiaoke-dev) | **VuePress SSG** | 从 `app.js` 挖路由与中文层级、实测回写、`examples/` |

---

## 适用与不适用

**适用**：厂商知识库、产品文档站、社区专题 —— 需要"把整本手册搬下来且能搜"。
**不适用**：单篇文档抓取、需要登录且拿不到凭据的站、结构化 API（直接用 API 更好）。

---

## 目录结构

```
manual-baking/
├── SKILL.md                # 面向 AI 的执行细则（八步流程 + 踩坑清单）
├── README.md               # 本文件（面向人的概览）
├── kbkit/                  # 通用工具包（零依赖，可 vendoring 进任意知识库）
├── scripts/                # search.py / build_index.py（CLI 薄封装）
├── tools/                  # fetch_manual.py / selftest.py / siteprobe.py
│   └── kb_new.py           #   脚手架：一条命令起一个新知识库
└── templates/              # 新知识库骨架
    ├── knowledge.json      #   站点配置模板（带逐项注释）
    ├── SKILL.md.tmpl
    ├── README.md.tmpl
    └── gitignore.tmpl
```

---

## 安装

本仓库既是**可安装的 skill**，也是**可复用的工具包**。

作为 skill 安装（两套客户端都要装，否则另一个里看不到）：

```bash
git clone https://github.com/silent-reader-cn/manual-baking.git \
  ~/.workbuddy-ai/skills/manual-baking
git clone https://github.com/silent-reader-cn/manual-baking.git \
  ~/.workbuddy/skills/manual-baking
```

作为工具包使用：直接跑 `tools/kb_new.py` 生成新知识库，或把 `kbkit/` 复制进已有仓库。

---

## 免责声明

本技能只描述**方法论与通用踩坑**，不包含任何厂商的文档内容或凭据。
用它抓取的内容版权归各厂商所有，请遵守目标站点的许可与数据合规要求。

## License

[MIT](./LICENSE)
