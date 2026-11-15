"""Figure for Section 4.2 (settlement calendar, M2): when redemptions burn.

Reads only the frozen M2 output data/processed/freeze_m2_calendar.csv
(vintage freeze_2026-10-16, window 2026-01-01 to 2026-09-30 ET) and draws,
per fund, the share of burn transactions by America/New_York hour, with the
non-business share of issuance and redemption value in each panel title.

Writes figures/fig_calendar.pdf and figures/fig_calendar.png and prints the
burn counts used in the text (modal hour count, total burns).

Usage: python scripts/11_fig_calendar.py
"""
import csv
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "processed", "freeze_m2_calendar.csv")
OUT = os.path.join(ROOT, "figures", "fig_calendar")
ORDER = ["FOBXX", "WTGXX", "JLTXX", "FYOXX"]
BAR = "#2a6f97"
MODAL = "#0b3954"
INK = "#333333"
MUTED = "#777777"


def load():
    rows = {}
    with open(SRC, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            counts = {h: 0 for h in range(24)}
            for item in r["burn_hour_counts"].split(";"):
                if item:
                    h, n = item.split(":")
                    counts[int(h)] = int(n)
            rows[r["fund"]] = dict(counts=counts,
                                   nonbusiness=float(r["nonbusiness_share_pct"]),
                                   modal=int(r["modal_burn_hour_et"]))
    return rows


def main():
    data = load()
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": INK, "ytick.color": INK})
    fig, axes = plt.subplots(2, 2, figsize=(7.0, 5.0), sharex=True, sharey=True)
    for ax, fund in zip(axes.flat, ORDER):
        d = data[fund]
        total = sum(d["counts"].values())
        hours = list(range(24))
        shares = [100 * d["counts"][h] / total for h in hours]
        colors = [MODAL if h == d["modal"] else BAR for h in hours]
        ax.bar(hours, shares, width=0.8, color=colors, edgecolor="white", linewidth=0.5)
        ax.axvspan(9, 17, color="#e8e8e8", zorder=0, linewidth=0)
        ax.set_title(f"{fund}: {total} burns; non-business share of\n"
                     f"issuance and redemption value {d['nonbusiness']:.4f}%",
                     fontsize=8, color=INK, loc="left")
        m = d["modal"]
        ax.annotate(f"{m:02d}:00 ET, {shares[m]:.0f}%", xy=(m, shares[m]),
                    xytext=(m + (1.2 if m < 18 else -1.2), min(shares[m] + 4, 70)),
                    ha="left" if m < 18 else "right", fontsize=8, color=INK)
        ax.set_ylim(0, 100)
        ax.set_xlim(-0.7, 23.7)
        ax.set_xticks(range(0, 24, 3))
        ax.grid(axis="y", color="#dddddd", linewidth=0.5)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        print(f"{fund}: total burns {total}; modal hour {m:02d} ET with {d['counts'][m]} burns "
              f"({shares[m]:.1f}%); non-business share {d['nonbusiness']}%")
    for ax in axes[1]:
        ax.set_xlabel("Hour of burn transaction (America/New_York)")
    for ax in axes[:, 0]:
        ax.set_ylabel("Share of burns (%)")
    fig.tight_layout()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT + ".pdf")
    fig.savefig(OUT + ".png", dpi=300)
    print("wrote", OUT + ".pdf", "and", OUT + ".png")


if __name__ == "__main__":
    main()
