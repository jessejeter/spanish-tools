"""Read-only: show the Inbox tab."""
import analyze_vocab as av
for r in av.read_sheet(av.get_sheets_service(), "Inbox!A:E"):
    r = r + [""] * 5
    print(f"  {r[0][:55]!r}\n     added: {r[1]} | flag: {r[2]} | processed: {r[3]} | E set: {bool(r[4])}")
