"""Read-only: show the Inbox tab after processing."""
from forecast import fetch_sheet
for r in fetch_sheet("Inbox"):
    r = r + [""] * 4
    print(f"  {r[0][:60]!r}\n     added: {r[1]}  | flag: {r[2]}  | processed: {r[3]}")
