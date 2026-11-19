import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
fig, ax = plt.subplots(figsize=(11, 5.2)); ax.set_xlim(0, 100); ax.set_ylim(0, 52); ax.axis("off")
# MultiXact ID axis
ax.annotate("", xy=(97, 30), xytext=(3, 30), arrowprops=dict(arrowstyle="->", lw=2))
ax.text(50, 33.5, "MultiXact IDs (and their members) in allocation order", ha="center", fontsize=11)
def mark(x, label, color, y0=30):
    ax.plot([x, x], [y0-6, y0+2], color=color, lw=3)
    ax.text(x, y0-8.5, label, ha="center", va="top", fontsize=9, color=color)
mark(12, "min(datminmxid)\nover ALL databases\n(template0 included)", "tab:red")
mark(40, "min(OldestMemberMXactId,\nOldestVisibleMXactId)\nover ALL backends", "tab:purple")
mark(92, "nextMXact", "black")
ax.add_patch(FancyBboxPatch((3, 36.5), 9, 6, boxstyle="round,pad=0.3", fc="#dff0d8", ec="green"))
ax.text(7.5, 39.5, "truncatable", ha="center", fontsize=9)
ax.add_patch(FancyBboxPatch((13, 36.5), 26, 6, boxstyle="round,pad=0.3", fc="#fcf8e3", ec="orange"))
ax.text(26, 39.5, "freezable by VACUUM, but files stay\nuntil every database's datminmxid passes", ha="center", fontsize=9)
ax.add_patch(FancyBboxPatch((41, 36.5), 50, 6, boxstyle="round,pad=0.3", fc="#f2dede", ec="red"))
ax.text(66, 39.5, "cannot be frozen: some backend may still need these MultiXacts", ha="center", fontsize=9)
ax.text(50, 6, "Floor 1 (truncation): vacuumdb skips template0 (datallowconn=false); autovacuum does not.\n"
        "Floor 2 (freezing): set when a backend first locks, updates or deletes a row; no database dimension,\n"
        "so an idle session in any database holds it for every MultiXact created after its lock.",
        ha="center", fontsize=9.5, family="monospace")
ax.set_title("Two cluster-wide floors decide when MultiXact member space can be released (PostgreSQL 18 and 19)", fontsize=11)
fig.tight_layout(); fig.savefig("charts/floors.png", dpi=150)
