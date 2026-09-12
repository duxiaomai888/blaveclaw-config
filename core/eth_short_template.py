"""
ETH 短期回测模板 - 1 个月做多做空对比
========================================
用法: python core/eth_short_template.py [SYMBOL] [--days N] [--hold H]

例:
  python core/eth_short_template.py ETHUSDT --days 30
  python core/eth_short_template.py BTCUSDT --days 30
  python core/eth_short_template.py SOLUSDT --days 14
"""
import argparse
import _bootstrap  # noqa: F401  — sys.path setup

import single_symbol_backtest as ssb


def main():
    parser = argparse.ArgumentParser(
        description='短期回测模板 - 做多做空对比',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  ETH 1 个月:  python core/eth_short_template.py ETHUSDT --days 30
  BTC 2 周:    python core/eth_short_template.py BTCUSDT --days 14
  SOL 1 周:    python core/eth_short_template.py SOLUSDT --days 7

输出:
  - Top 20 规则(按 Sharpe 排序)
  - 双方向对比(long 收益 vs short 收益)
  - ★★★ 实战推荐
  - SUMMARY(触发数、胜率、方向翻转)
        """
    )
    parser.add_argument('symbol', help='交易对 e.g. ETHUSDT')
    parser.add_argument('--days', type=int, default=30, help='回测天数 (default 30)')
    parser.add_argument('--hold', type=int, default=12, help='持有 K 线数 (default 12)')
    parser.add_argument('--top', type=int, default=20, help='Top N (default 20)')
    parser.add_argument('--cats', nargs='+', default=None, help='规则类别 e.g. A B D')
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"  短期回测模板 | {args.symbol} | {args.days}d | hold={args.hold}")
    print(f"{'='*70}\n")

    df = ssb.run_single_symbol(
        symbol=args.symbol,
        days=args.days,
        hold_bars=args.hold,
        categories=args.cats,
        top_n=args.top,
        verbose=True
    )


if __name__ == '__main__':
    main()
