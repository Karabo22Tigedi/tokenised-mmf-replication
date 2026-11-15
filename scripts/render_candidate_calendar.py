"""Render a more legible candidate figure from unchanged frozen M2 counts.

This presentation-only renderer preserves scripts/11_fig_calendar.py and its
original figures. It writes only figures/fig_calendar_revised.{pdf,png}.
"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data/processed/freeze_m2_calendar.csv"
OUTPUT = ROOT / "figures/fig_calendar_revised"
ORDER = ("FOBXX", "WTGXX", "JLTXX", "FYOXX")


def main() -> None:
    with SOURCE.open(encoding="utf-8", newline="") as handle:
        rows = {row["fund"]: row for row in csv.DictReader(handle)}
    plt.rcParams.update({"font.size": 10.5, "axes.edgecolor": "#777777",
                         "axes.labelcolor": "#333333", "xtick.color": "#333333",
                         "ytick.color": "#333333", "xtick.labelsize": 10,
                         "ytick.labelsize": 10, "pdf.fonttype": 42})
    fig, axes = plt.subplots(2, 2, figsize=(6.35, 5.35), sharex=True,
                             sharey=True, layout="constrained")
    for ax, fund in zip(axes.flat, ORDER):
        row = rows[fund]
        counts = {hour: 0 for hour in range(24)}
        for item in row["burn_hour_counts"].split(";"):
            if item:
                hour, number = item.split(":")
                counts[int(hour)] = int(number)
        total = sum(counts.values())
        modal = int(row["modal_burn_hour_et"])
        shares = [100 * counts[hour] / total for hour in range(24)]
        ax.axvspan(9, 17, color="#e8e8e8", zorder=0, linewidth=0)
        ax.bar(range(24), shares, width=0.8,
               color=["#0b3954" if hour == modal else "#2a6f97" for hour in range(24)],
               edgecolor="white", linewidth=0.5)
        ax.set_title(f"{fund}: {total} burns\nNon-business value: "
                     f"{float(row['nonbusiness_share_pct']):.4f}%",
                     fontsize=10.5, color="#333333", loc="left", pad=8)
        ax.annotate(f"{modal:02d}:00 ET, {shares[modal]:.0f}%",
                    xy=(modal, shares[modal]),
                    xytext=(modal + (1.0 if modal < 18 else -1.0),
                            min(shares[modal] + 7, 71)),
                    ha="left" if modal < 18 else "right",
                    fontsize=10, color="#333333")
        ax.set(ylim=(0, 100), xlim=(-0.7, 23.7), xticks=range(0, 24, 3))
        ax.grid(axis="y", color="#dddddd", linewidth=0.5)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        print(f"{fund}: {total} burns, modal {modal:02d} ET, "
              f"modal share {shares[modal]:.1f}%, "
              f"non-business value {float(row['nonbusiness_share_pct']):.4f}%")
    fig.supxlabel("Hour of burn transaction (Eastern Time)", fontsize=11)
    fig.supylabel("Share of burns (%)", fontsize=11)
    fig.savefig(OUTPUT.with_suffix(".pdf"))
    fig.savefig(OUTPUT.with_suffix(".png"), dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main()
