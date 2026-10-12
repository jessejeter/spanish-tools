import os
import analyze_vocab as av
notes = [l.strip() for l in open("inbox_notes.txt", encoding="utf-8") if l.strip()]
av.process_inbox(av.get_sheets_service(), os.environ["GEMINI_API_KEY"], dry_run=True, notes=notes)
