"""One-off: create the Inbox tab and put the learner's first notes in it (writes Inbox only)."""
import analyze_vocab as av
service = av.get_sheets_service()
av.ensure_inbox_sheet(service)
existing = {r[0].strip() for r in av.read_sheet(service, f"{av.INBOX_SHEET}!A:A")[1:] if r}
notes = [l.strip() for l in open("inbox_notes.txt", encoding="utf-8") if l.strip() and l.strip() not in existing]
if notes:
    av.execute_with_retry(service.spreadsheets().values().append(
        spreadsheetId=av.SPREADSHEET_ID, range=f"{av.INBOX_SHEET}!A1", valueInputOption="RAW",
        insertDataOption="INSERT_ROWS", body={"values": [[n] for n in notes]}))
print(f"Inbox seeded with {len(notes)} notes")
