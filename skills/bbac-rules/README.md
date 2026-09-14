# BBAC-Rules Skill

59 条结构化交易规则的市场扫描技能包 —— 覆盖 13 个维度（A–M），用 Blave alpha 指标
（HC / WH / TI / MS / LM / SM）+ OHLCV + Volume 定义精确入场条件，并附带方向校准结果。

- **规则代码唯一真理源**：`rules_catalog/catalog.py` —— 本技能包里的 markdown 是映射，不是源
- **扫描执行器**：`core/coin_screener.py`（技能包只定义规则 + 读结果，本身不含可执行代码）
- **不下单**：本技能只扫描并报告信号，决策权在用户手上
- 版本 `1.1.0`

---

## 安全

本技能包是**纯文档** —— 全是 Markdown，不含脚本、二进制或依赖。
所有 API 调用由 agent 按文档自己写代码发出（走 `lib.data` / `core/coin_screener.py`），
API key 留在本地 `.env`，本技能包不读取、不传输任何 key。

建议：使用最小权限的 Blave key（只需行情数据，无需交易权限）。

本技能包**不会**：下单、撤单、转仓、改杠杆。

---

## 安装

### OpenClaw / ClawHub（`skills/` 约定）

技能包已在仓库的 `skills/bbac-rules/`，由 `skills-lock.json` 以 `sourceType: local` 登记。

### Claude Code（`.claude/skills/` 约定）

Claude Code 只扫描 `.claude/skills/<name>/SKILL.md`，**不看仓库根的 `skills/`**。
而本仓库的 `.gitignore` 把整个 `.claude/` 排除了 —— 所以用**目录 junction** 做桥，
避免同一份内容在 git 里出现两份：

```powershell
New-Item -ItemType Junction -Path '.claude\skills\bbac-rules'  -Target '.\skills\bbac-rules'
New-Item -ItemType Junction -Path '.claude\skills\blave-quant' -Target '.\skills\blave-quant'   # 可选：指标解读文档
```

Linux / macOS 上改用：

```bash
mkdir -p .claude/skills
ln -s ../../skills/bbac-rules  .claude/skills/bbac-rules
ln -s ../../skills/blave-quant .claude/skills/blave-quant
```

> ⚠️ **Windows 上不要用 Git Bash 的 `ln -s`。** 当 NTFS 符号链接创建被拒绝时它
> 会**静默退化成拷贝** —— 看起来成功了（exit 0），但建出来的是目录树副本，
> 此后每次改 `skills/` 下的内容，`.claude/skills/` 里的副本都不会跟着变，
> 于是 Claude Code 读到的是旧版技能。用 `dir /AL .claude\skills` 或
> PowerShell 的 `LinkType` 列验证：junction 会显示出来，真目录不会。

新克隆后重跑一次上面两行即可 —— junction 本身不被 git 跟踪，所以 clone 不会自动带上。

装完在**新会话**里确认：技能列表应出现 `bbac-rules`，`/bbac-rules` 可调用。

---

## 能力

| # | 能力 |
|---|---|
| 1 | 59 条规则完整目录（条件、类别、skip 原因） |
| 2 | 方向校准表 —— 24/58 条已回测规则方向翻转（19 A–J + 5 K/L/M 小样本） |
| 3 | 跨周期稳健性 —— 13 条 Stab=100% |
| 4 | 强推规则 —— B04 / B01（双周期 Sharpe ≥ 3.5） |
| 5 | 批量扫描流程 + 限流 / 失败处理 + 输出读法 |

## 参考文件

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 主入口：核心能力、扫描流程、可运行最小示例 |
| `references/rules-catalog.md` | 59 条规则完整定义 + 方向校准表 + 数据列需求 |
| `references/scanning-guide.md` | 扫描命令、参数表、输出三段读法、限流与静默失效防线 |

## 依赖

| 依赖 | 必需 | 说明 |
|---|---|---|
| `.env` 中 `blave_api_key` / `blave_secret_key` | ✅ | 支持 `_key2 .. _key8` 多 key 轮询 |
| `core/coin_screener.py` + `rules_catalog/catalog.py` | ✅ | 数据入口与规则代码 |
| `blave-quant` skill | ❌ 可选 | 仅用于指标含义解读；数据层不经过它 |

## 已知限制

- K/L/M 9 条新规则校准样本小（每规则 1–7 个币种），方向置信度低于 A–J
- M01 触发不足 min_trades，无回测数据，`direction_best` 回退到 `direction_doc`
- J 类 3 条 + E05/E06 共 5 条 skip（需 BTC 或多币板块数据，当前扫描器未接）
- `skills-lock.json` 的 `computedHash` 由外部技能管理器计算，本仓库无对应工具；
  改动 SKILL.md 后本地无法自行刷新该哈希
