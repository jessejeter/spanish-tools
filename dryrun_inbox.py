"""Read-only: show the Inbox tab after processing."""
import analyze_vocab as av
for r in av.read_sheet(av.get_sheets_service(), "Inbox!A:D"):
    r = r + [""] * 4
    print(f"  {r[0][:60]!r}\n     added: {r[1]}  | flag: {r[2]}  | processed: {r[3]}")
