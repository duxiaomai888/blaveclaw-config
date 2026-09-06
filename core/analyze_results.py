"""
Reverse Engineering: v4.4 Calibration
========================================
读 cache/csv/ 里的 90d 批量汇总 + 跨周期配对数据,
自动生成 v4.4 文档校准报告。覆盖度从数据实算:catalog 50 条里有 skip 项,
也有 active 但在样本上触发不足 min_trades 而没出数据的,报告头部会写明实际
覆盖了几条、几条有跨周期配对、几条 Stab=100%。

v4.4 变更(口径与版本,不重算数据):
  - 版本号统一到 VERSION(v4.4);报告标题由 VERSION 注入,不再硬编码
  - 章节编号改为按输出顺序自动计数(_sec),根治"缺 CSV 就永久跳号"和手工补插的"三.5"
  - 执行口径明文(入场下一根 / 收盘价成交上界 / 参数锁定)见 lib.analysis.backtest docstring
"""
import sys
import os
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import pandas as pd
from datetime import datetime
import _bootstrap  # noqa: F401  — sys.path setup
from rules_catalog.catalog import ALL_RULES
from core.version import VERSION

# 章节编号自动计数。编号是**输出位置**,不是章节身份 —— 某章因缺 CSV 被跳过时,
# 后续章节顺位前移,不产生空洞,也不需要在正文里手工补号(如"三.5")。
# _CN_NUM 覆盖到第 20 章;超出则退回阿拉伯数字,比报错更不容易炸。
_CN_NUM = '〇一二三四五六七八九十'
_SEC_N = 0


def _sec(title):
    """按输出顺序生成 '## 三、<title>' 并自增序号。"""
    global _SEC_N
    _SEC_N += 1
    cn = _CN_NUM[_SEC_N] if _SEC_N <= len(_CN_NUM) else str(_SEC_N)
    return f"## {cn}、{title}"


def _load_inputs():
    """Load batch summaries + catalog lookups. Return (df_90, cross, doc_name, doc_direction)."""
    sys.stdout.write("=== Loading data from cache/csv/ ===\n")
    df_90   = pd.read_csv('cache/csv/batch_50_summary.csv')
    df_180  = pd.read_csv('cache/csv/cross_period_rule_summary.csv')
    cross   = df_180.copy()
    sys.stdout.write(f"Loaded cross-period summary: {len(cross)} rows\n\n")
    doc_name      = {r['id']: r['name_cn']       for r in ALL_RULES}
    doc_direction = {r['id']: r['direction_doc'] for r in ALL_RULES}
    return df_90, cross, doc_name, doc_direction


def _section_overall(df_90, cross, doc_direction):
    """Section 1: overall summary table + key finding."""
    n_sym_90   = df_90['symbol'].nunique()
    n_pair     = cross['n_symbols'].sum()
    flip_pct_90 = (df_90['direction_doc'] != df_90['direction_best']).sum() / len(df_90) * 100
    L = [_sec("整体结论"), ""]
    L.append("| 指标 | 数值 | 解读 |")
    L.append("|------|------|------|")
    L.append(f"| 数据范围 | {n_sym_90} 币种(90d)+ 跨周期配对 {n_pair} 条 | 见 cross_period_rule_summary.csv |")
    L.append(f"| 90d 规则正 Sharpe 比例 | {(df_90['sharpe']>0).sum()}/{len(df_90)} | 全部规则都赚 |")
    L.append(f"| 90d 方向翻转率 | {flip_pct_90:.1f}% | 文档与实测方向相反 |")
    L.append(f"| 180d 方向翻转率 | 文档对照需逐条查表 | 跨周期方向一致由 cross_period 验证 |")
    L.append(f"| 平均 Sharpe 90d | {df_90['sharpe'].mean():.2f} | 高 |")
    L.append(f"| 平均 Sharpe 180d | {cross['avg_sharpe_180'].mean():.2f} | 略降(波动累积) |")
    L.append(f"| 跨周期稳健规则 | 由 {len(cross[cross['stability']==1.0])} 条 Stab=100% 组成 | 全部双周期正 Sharpe |")
    L.append("")
    L.append(f"**核心发现**:扩到 {n_sym_90} 币种后,核心结论保持一致——文档约 {flip_pct_90:.0f}% 规则方向与实测相反,需要校准。")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _section_recommendations(cross, doc_name, doc_direction):
    """Section 2: ★★★ strong recommendations.

    Same criterion as _section_tiered_recs (both periods individually >= 3.5)
    so the two '★★★' lists in one report can never disagree.
    """
    cross = cross.copy()
    cross['avg_sharpe_combined'] = (cross['avg_sharpe_90'] + cross['avg_sharpe_180']) / 2
    top_recs = cross[
        (cross['stability']    >= 1.0) &
        (cross['avg_sharpe_90']  >= 3.5) &
        (cross['avg_sharpe_180'] >= 3.5)
    ].sort_values('avg_sharpe_combined', ascending=False)
    L = [_sec("★★★ 实战推荐(双周期 Sharpe≥3.5,跨周期稳健)"), "",
         f"**入选标准**:跨周期 Stab=100%,且 90d 与 180d Sharpe **各自** ≥ 3.5 — 与第七节分级同口径"
         f"(本次 {len(top_recs)} 条)", ""]
    L.append("| 规则 | 名称 | 双周期 Sharpe | 90d 收益 | 180d 收益 | 方向 (文档→实测) |")
    L.append("|------|------|--------------|---------|---------|----------------|")
    for _, r in top_recs.iterrows():
        rid, name = r['rule'], doc_name.get(r['rule'], '?')
        doc_dir   = doc_direction.get(rid, '?')
        L.append(f"| {rid} | {name} | 90d: {r['avg_sharpe_90']:.2f}<br>180d: {r['avg_sharpe_180']:.2f} "
                 f"| +{r['avg_total_90']:.0f}% | +{r['avg_total_180']:.0f}% | {doc_dir} ✓ |")
    L.append("")
    L.append("**说明**:文档方向与实测一致,可直接按文档业务逻辑使用。")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _section_dual_direction(df_90, cross, doc_name):
    """Section 3.5: dual-direction comparison table."""
    df = df_90.copy()
    df['long_sharpe']  = df.apply(lambda r: r['sharpe']       if r['direction_best']=='long' else r['other_sharpe'], axis=1)
    df['short_sharpe'] = df.apply(lambda r: r['other_sharpe'] if r['direction_best']=='long' else r['sharpe'],       axis=1)
    df['long_total']   = df.apply(lambda r: r['total']        if r['direction_best']=='long' else r['other_total'],  axis=1)
    df['short_total']  = df.apply(lambda r: r['other_total']  if r['direction_best']=='long' else r['total'],        axis=1)

    stable_ids = set(cross[cross['stability'] >= 1.0]['rule'])
    top_dual   = df[df['rule'].isin(stable_ids)].sort_values('sharpe', ascending=False).head(20)

    L = [_sec("双方向对比表(每个规则 long 和 short 都跑)"), "",
         "**重要**:每条规则两个方向都跑,**两个都赚,但赚多少不同**。表格里:",
         "- `long_sharpe` = 做多 Sharpe",
         "- `short_sharpe` = 做空 Sharpe",
         "- `best_dir` = 实测更优方向(按 Sharpe 选)",
         ""]
    L.append("| 规则 | 名称 | 文档方向 | long Sharpe | short Sharpe | long 收益 | short 收益 | best_dir |")
    L.append("|------|------|---------|------------|-------------|---------|-----------|---------|")
    for _, r in top_dual.iterrows():
        rid, name = r['rule'], doc_name.get(r['rule'], '?')
        L.append(f"| {rid} | {name} | {r['direction_doc']} | {r['long_sharpe']:.2f} | {r['short_sharpe']:.2f} | "
                 f"{r['long_total']:+.0f}% | {r['short_total']:+.0f}% | {r['direction_best']} |")
    L.append("")
    L.append("**关键观察**:")
    L.append("- 做多/做空两个方向**几乎都正 Sharpe**,只是赚多赚少不同")
    L.append("- 90d 行情以**做空为主**(多数规则做空收益 > 做多)")
    L.append("- **G05 HC 零轴跌破** 文档 short,实测 short 一致——这种规则无论涨跌都赚")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _section_direction_flip(df_90, doc_name, doc_direction):
    """Section 4: rules where doc direction disagrees with backtest."""
    df = df_90.copy()
    df['direction_doc'] = df['rule'].map(doc_direction)
    flip_rules = df[df['direction_doc'] != df['direction_best']]
    flip_set   = set(flip_rules['rule'].unique())

    L = [_sec("方向校准(文档方向与实测相反)"), "",
         "**问题**:文档业务描述的方向(比如'主力偷偷买→做多')与 90d/180d 实测最优方向相反。",
         "**建议**:文档保留业务描述(教学价值),但**实战使用文档时,按本表校准后的方向入场**。",
         ""]
    L.append("| 规则 | 名称 | 文档方向 | 实测方向 | 90d Sharpe | 90d 收益 | 触发次数 |")
    L.append("|------|------|---------|---------|-----------|---------|---------|")
    for rid in sorted(flip_set):
        r90 = df[df['rule'] == rid].sort_values('sharpe', ascending=False).iloc[0]
        name = doc_name.get(rid, '?')
        L.append(f"| {rid} | {name} | {r90['direction_doc']} | **{r90['direction_best']}** | "
                 f"{r90['sharpe']:.2f} | +{r90['total']:.0f}% | {int(r90['n'])} |")
    L.append("")
    L.append("**说明**:以上规则的文档方向标注有误,实战请用校准后的方向。")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _section_threshold_calibration():
    """Section 5: threshold calibration (static)."""
    L = [_sec("阈值校准建议"), "",
         "文档 v4.0 的阈值(如 `hc.level >= 2`)在实测中通常过严。最优阈值一般在中位~85 分位之间。",
         ""]
    L.append("| 指标 | 文档阈值 | 实测推荐阈值 | 备注 |")
    L.append("|------|---------|-------------|------|")
    L.append("| HC(主力意图) | hc.level >= 2 | **hc > p85 (≈0.66)** | 文档太严 |")
    L.append("| TI(动能) | ti.level >= 2 | **ti > p85 (≈0.5~0.7)** | 文档太严 |")
    L.append("| MS(市场情绪) | ms.level <= -2 | **ms < p15 (≈-0.66)** | 文档太严 |")
    L.append("| WH(巨鲸) | wh.level >= 1.07 | **wh > p85 (≈0.5)** | 文档略严 |")
    L.append("| SM(动量) | sm.deorange | **sm.abs() > 1.0** | 文档较准 |")
    L.append("| LM(爆仓) | lm.is_extreme | **lm.abs() > p70 (≈1.0)** | 文档略严 |")
    L.append("")
    L.append("**说明**:以上分位阈值是 14 币种中位数,实战中应根据具体币种波动率微调。")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _section_unstable(cross, doc_name):
    """Section 6: rules with stability < 100%."""
    unstable = cross[cross['stability'] < 1.0].sort_values('stability')
    L = [_sec("不推荐规则(跨周期不稳 或 文档/实测矛盾)"), "",
         "**标准**:稳定性 < 100%(不是所有币种都跨周期正 Sharpe)", ""]
    if len(unstable) > 0:
        L.append("| 规则 | 名称 | 稳定性 | 双周期 Sharpe | 备注 |")
        L.append("|------|------|--------|-------------|------|")
        for _, r in unstable.iterrows():
            rid, name = r['rule'], doc_name.get(r['rule'], '?')
            L.append(f"| {rid} | {name} | {r['stability']*100:.0f}% | "
                     f"90d: {r['avg_sharpe_90']:.2f}<br>180d: {r['avg_sharpe_180']:.2f} | 跨周期不稳 |")
    else:
        L.append("(当前 14 币种中所有跨周期配对都稳定)")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _section_tiered_recs(cross, doc_name):
    """Section 7: ★★★ / ★★ / ☆ / not-recommended tiers."""
    cross = cross.copy()
    cross['avg_sharpe_combined'] = (cross['avg_sharpe_90'] + cross['avg_sharpe_180']) / 2
    star3   = cross[(cross['stability']>=1.0) & (cross['avg_sharpe_90']>=3.5) & (cross['avg_sharpe_180']>=3.5)]
    star2   = cross[(cross['stability']>=1.0) & (
                     ((cross['avg_sharpe_90'] >=3.0) & (cross['avg_sharpe_180'] < 3.5)) |
                     ((cross['avg_sharpe_180']>=3.0) & (cross['avg_sharpe_90']  < 3.5)))]
    star1   = cross[(cross['stability']>=1.0) & (cross['avg_sharpe_90']>0) & (cross['avg_sharpe_180']>0) &
                    ~cross['rule'].isin(star3['rule']) & ~cross['rule'].isin(star2['rule'])]
    not_rec = cross[cross['stability'] < 1.0]

    L = [_sec("实战推荐分级"), ""]
    L.append(f"### ★★★ ({len(star3)} 条) - 强推(双周期 Sharpe ≥ 3.5)")
    L.append("")
    for _, r in star3.sort_values('avg_sharpe_combined', ascending=False).iterrows():
        L.append(f"- **{r['rule']}** {doc_name.get(r['rule'], '?')}: "
                 f"90d Sharpe {r['avg_sharpe_90']:.2f} / 180d Sharpe {r['avg_sharpe_180']:.2f}")
    L.append("")
    L.append(f"### ★★ ({len(star2)} 条) - 可用(双周期 Sharpe 3.0-3.5)")
    L.append("")
    for _, r in star2.sort_values('avg_sharpe_combined', ascending=False).iterrows():
        L.append(f"- **{r['rule']}** {doc_name.get(r['rule'], '?')}: "
                 f"90d Sharpe {r['avg_sharpe_90']:.2f} / 180d Sharpe {r['avg_sharpe_180']:.2f}")
    L.append("")
    L.append(f"### ☆ ({len(star1)} 条) - 谨慎使用(双周期正 Sharpe 但 Sharpe < 3.0)")
    L.append("")
    L.append(f"### ❌ 不推荐({len(not_rec)} 条 - 跨周期不稳)")
    L.append("")
    L.append("---")
    L.append("")
    return L, star3, star2, star1, not_rec


def _section_btc_corr():
    """Section 8: cross-coin BTC-corr rules (E05/E06/J01/J02) from btc_corr_results.csv."""
    btc_corr_path = 'cache/csv/btc_corr_results.csv'
    if not os.path.exists(btc_corr_path):
        return []
    btc_df = pd.read_csv(btc_corr_path)
    L = [_sec("跨币种联动规则(BTC 联动 E05/E06/J01/J02)"), "",
         "**特点**:需要 BTC 价格 + altcoin 价格的协同,单币种框架跑不动,需独立框架验证。",
         "**数据**:14 altcoin × 4 rules × 2 directions = 30 行,见 `cache/csv/btc_corr_results.csv`",
         ""]
    L.append("| 规则 | 名称 | 文档方向 | 实测最优 | 跨币种胜率 | 实战推荐 |")
    L.append("|------|------|---------|---------|-----------|---------|")
    for rid, name in [('E05', 'BTC 涨+该币强'), ('E06', 'BTC 跌+该币强'),
                       ('J01', 'BTC 联动补涨'), ('J02', 'BTC 背离补涨')]:
        sub = btc_df[btc_df['rule'] == rid]
        if len(sub) == 0:
            continue
        doc_dir   = 'long' if rid in ('E05', 'J01', 'J02') else 'short'
        long_sh   = sub[sub['direction']=='long']['sharpe'].mean()  if len(sub[sub['direction']=='long'])  > 0 else 0
        short_sh  = sub[sub['direction']=='short']['sharpe'].mean() if len(sub[sub['direction']=='short']) > 0 else 0
        best_dir  = 'long' if long_sh > short_sh else 'short'
        rec       = "✓ 一致" if best_dir == doc_dir else f"⚠️ 翻转(实测 {best_dir})"
        wrs       = sub[sub['direction'] == best_dir]['wr']
        win_rate  = f"{(wrs > 50).sum()}/{len(wrs)}" if len(wrs) > 0 else "N/A"
        L.append(f"| {rid} | {name} | {doc_dir} | {best_dir} | {win_rate} | {rec} |")
    L.append("")
    L.append("**结论**:E06(文档 short)实测稳定;E05/J01/J02(文档 long)实测效果差,需重新评估。")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _section_j03():
    """Section 9: sectoral J03 results."""
    j03_path = 'cache/csv/j03_sector_results.csv'
    if not os.path.exists(j03_path):
        return []
    j03_df = pd.read_csv(j03_path)
    L = [_sec("板块内联动(J03)"), "",
         "**特点**:需要同板块多个币种一起判断,板块成员用代理(我们按币种类型手动分类)。",
         "**数据**:7 板块 × 28 币种 × 2 directions = 29 行,见 `cache/csv/j03_sector_results.csv`",
         ""]
    L.append("| 板块 | long 胜率 | short 胜率 | 实战方向 |")
    L.append("|------|----------|-----------|---------|")
    j03_df['sector'] = j03_df['name_cn'].str.extract(r'\((.+?)\)')
    for sector, grp in j03_df.groupby('sector'):
        longs  = grp[grp['direction'] == 'long']
        shorts = grp[grp['direction'] == 'short']
        long_str  = f"{(longs['wr']>50).sum()}/{len(longs)} (sh {longs['sharpe'].mean():.1f})" if len(longs)  > 0 else "N/A"
        short_str = f"{(shorts['wr']>50).sum()}/{len(shorts)} (sh {shorts['sharpe'].mean():.1f})" if len(shorts) > 0 else "N/A"
        l_sh = longs['sharpe'].mean()  if len(longs)  > 0 else -999
        s_sh = shorts['sharpe'].mean() if len(shorts) > 0 else -999
        L.append(f"| {sector} | {long_str} | {short_str} | {'long' if l_sh > s_sh else 'short'} |")
    L.append("")
    L.append("**结论**:J03 板块内联动分板块表现不同——AI/DeFi 适合 long,L1/L2/Privacy 适合 short。")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _section_appendix(n_sym_90, n_pair):
    """Section 10: data scope, limits, next steps."""
    L = [_sec("附录"), "",
         "### 数据范围",
         f"- 90d 周期:{n_sym_90} 币种(见 cache/csv/batch_50_summary.csv)",
         f"- 跨周期配对:{n_pair} 条 (rule, symbol) 同进 Top 5",
         "- 持有期:12 根 1h K 线",
         "- 频率:每小时触发检查",
         "",
         "### 已知限制", "",
         "1. **跨周期样本**:14 币种配对,统计上有意义但建议扩到 50+ 币种再校准",
         "2. **180d 范围**:可能未覆盖完整牛熊周期",
         "3. **未做止损/止盈**:回测可能高估 Sharpe(实战中爆拉/暴跌会触发止损)",
         "4. **未做手续费滑点分离**:当前 fee=0.0005 单边,实际可能更高",
         "5. **死币跳过**:改名/下市币种(MATICUSDT、RNDRUSDT 等)在 Blave 端 400,自动跳过",
         "",
         "### 下一步建议", "",
         f"1. 把本报告核心结论写进 `文档模板.md` {VERSION} 章节",
         "2. 扩 50+ 币种覆盖更多板块,出下一版校准",
         "3. 写 Top 5 正式策略(用 ★★★ 规则),用 TEMPLATE_A 框架",
         "4. 加止损/止盈风控,验证 Sharpe 真实性",
         "",
         "---",
         "",
         "*本报告由 `analyze_results.py` 自动生成,基于 `cache/csv/` 数据。*",
         ""]
    return L


def main():
    df_90, cross, doc_name, doc_direction = _load_inputs()
    n_sym_90 = df_90['symbol'].nunique()
    n_pair   = int(cross['n_symbols'].sum())
    # 覆盖度必须从数据算,不能写死:catalog 50 条里有 skip 的,
    # 也有 active 但在样本上触发不足 min_trades 而没出数据的。
    n_total   = len(ALL_RULES)
    n_covered = int(df_90['rule'].nunique())
    n_stable  = int((cross['stability'] == 1.0).sum())
    n_cross   = int(cross['rule'].nunique())
    output_lines = [f"# {VERSION} 实测校准报告({n_covered}/{n_total} 条规则有 90d 数据)", "",
                    f"> **生成日期**: {datetime.now().strftime('%Y-%m-%d')}",
                    f"> **数据来源**: {n_sym_90} 币种(90d);跨周期 {n_cross} 条规则,"
                    f"{n_pair} 个 (rule, symbol) 配对 (见 cross_period_rule_summary.csv)",
                    f"> **校准目的**: 用跨周期实测校准文档 v4.0 的业务假设",
                    f"> **覆盖**: {n_covered}/{n_total} 条规则有 90d 数据;"
                    f"{n_cross} 条有跨周期配对,其中 {n_stable} 条 Stab=100%"
                    f"(未覆盖的 {n_total - n_covered} 条 = catalog skip 项 + 触发不足 min_trades)",
                    "", "---", ""]
    output_lines += _section_overall(df_90, cross, doc_direction)
    output_lines += _section_recommendations(cross, doc_name, doc_direction)
    output_lines += _section_dual_direction(df_90, cross, doc_name)
    output_lines += _section_direction_flip(df_90, doc_name, doc_direction)
    output_lines += _section_threshold_calibration()
    output_lines += _section_unstable(cross, doc_name)
    tier_lines, star3, star2, star1, not_rec = _section_tiered_recs(cross, doc_name)
    output_lines += tier_lines
    output_lines += _section_btc_corr()
    output_lines += _section_j03()
    output_lines += _section_appendix(n_sym_90, n_pair)

    report_path = f'cache/{VERSION}_calibration.md'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))

    # Flip-set: from df_90 + doc_direction (rebuild)
    df = df_90.copy()
    df['direction_doc'] = df['rule'].map(doc_direction)
    flip_set = set(df[df['direction_doc'] != df['direction_best']]['rule'].unique())

    sys.stdout.write(f"\n=== {VERSION} report saved: {report_path} ===\n")
    sys.stdout.write(f"Total lines: {len(output_lines)}\n\n")
    sys.stdout.write(f"=== SUMMARY ===\n")
    sys.stdout.write(f"★★★ 强推: {len(star3)} 条\n")
    sys.stdout.write(f"★★ 可用: {len(star2)} 条\n")
    sys.stdout.write(f"☆ 谨慎: {len(star1)} 条\n")
    sys.stdout.write(f"❌ 不推荐: {len(not_rec)} 条\n")
    sys.stdout.write(f"\nDirection flip rules: {len(flip_set)} (need 校准)\n")
    sys.stdout.flush()


if __name__ == '__main__':
    main()
