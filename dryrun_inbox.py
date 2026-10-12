"""One-off: re-queue Inbox notes that were skipped as duplicates (clears their B:D only)."""
import analyze_vocab as av
service = av.get_sheets_service()
rows = av.read_sheet(service, "Inbox!A:D")
clears = []
for i, r in enumerate(rows[1:], start=2):
    r = r + [""] * 4
    if r[1].startswith("Already in Sheet1"):
        print("re-queue:", r[0])
        clears.append(f"Inbox!B{i}:D{i}")
if clears:
    av.execute_with_retry(service.spreadsheets().values().batchClear(
        spreadsheetId=av.SPREADSHEET_ID, body={"ranges": clears}))
print(len(clears), "re-queued")
